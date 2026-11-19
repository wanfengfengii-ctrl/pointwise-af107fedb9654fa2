import argparse
import json
import sqlite3
import unittest
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from tempfile import TemporaryDirectory

from archive.pairing import PairResult, initialize_pairing, load_pair_result
from archive.publication import PublishedSample
from archive.retrieval import retrieve
from archive.store import ArchiveStore
from control.active import initialize_active
from control.dispatch import advance
from control.progression import StepResult
from control.recovery import resume
from device.latch import DeviceSample
from device.port import DevicePort
from device.recording import DeviceRecording
from measurement.calibration import initialize_calibrations, install_calibration, load_calibration
from measurement.conversion import MeasuredValue, convert_reading
from measurement.profiles import CalibrationProfile, build_profile
from orders.admission import submit_orders
from orders.batches import BatchBinding, initialize_batches, load_batch, register_batch
from orders.bottles import BottleJob, validate_identifier
from orders.pairs import initialize_pairs, submit_pair
from orders.queue import initialize_queue, jobs_after


DEFAULT_PROFILE = build_profile('ntu-v1', 0, 31, '3/8', '1/4', 'NTU')


@dataclass(frozen=True)
class OrderAdmission:
    job: BottleJob
    sample: PublishedSample | None


class Workbench:
    def __init__(self, db: sqlite3.Connection, device: DevicePort | None):
        self.db = db
        self.device = device
        self.archive = ArchiveStore(db)

    @classmethod
    def open(cls, path: str | Path, *, device_path: str | Path | None = None,
             readings: dict[str, object] | None = None, seed: tuple[int, object] | None = None,
             automatic: bool = True) -> 'Workbench':
        db = sqlite3.connect(path)
        recording = None
        try:
            db.execute('PRAGMA foreign_keys = ON')
            initialize_calibrations(db)
            initialize_batches(db)
            initialize_queue(db)
            initialize_pairs(db)
            initialize_active(db)
            ArchiveStore(db).initialize()
            initialize_pairing(db)
            install_calibration(db, DEFAULT_PROFILE)
            if device_path is None:
                if readings is not None or seed is not None:
                    raise ValueError('a recording requires a device database')
                return cls(db, None)
            recording = DeviceRecording(device_path)
            recording.initialize(readings, seed)
            return cls(db, DevicePort(recording, automatic))
        except BaseException:
            if recording is not None:
                recording.close()
            db.close()
            raise

    def __enter__(self) -> 'Workbench':
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def close(self) -> None:
        if self.device is not None:
            self.device.recording.close()
            self.device = None
        self.db.close()

    def install_calibration(self, profile: CalibrationProfile) -> CalibrationProfile:
        return install_calibration(self.db, profile)

    def register_batch(self, batch_id: str, profile_id: str) -> BatchBinding:
        return register_batch(self.db, batch_id, profile_id)

    def submit_orders(self, batch_id: str, items: list | tuple) -> list[OrderAdmission]:
        jobs = submit_orders(self.db, batch_id, items)
        return self.show_signed_replays(jobs)

    def show_signed_replays(self, jobs: list[BottleJob]) -> list[OrderAdmission]:
        return [OrderAdmission(job, self.retrieve(job.order_id)) for job in jobs]

    def submit_pair(self, batch_id: str, pair_id: str, members: list | tuple,
                    threshold: object) -> tuple[BottleJob, BottleJob]:
        return submit_pair(self.db, batch_id, pair_id, members, threshold)

    def advance(self, batch_id: str) -> StepResult:
        return advance(self.db, self.device, batch_id)

    def resume(self, batch_id: str) -> StepResult:
        return resume(self.db, self.device, batch_id)

    def on_notices(self, batch_id: str) -> StepResult | None:
        if self.device is None or not self.device.drain_notices():
            return None
        return self.resume(batch_id)

    def retrieve(self, order_id: str) -> PublishedSample | None:
        return retrieve(self.archive, order_id)

    def retrieve_pair(self, pair_id: str) -> PairResult | None:
        validate_identifier(pair_id, 'pair ID')
        return load_pair_result(self.db, pair_id)

    def recompute(self, order_id: str) -> MeasuredValue | None:
        sample = self.retrieve(order_id)
        if sample is None:
            return None
        binding = load_batch(self.db, sample.batch_id)
        return convert_reading(load_calibration(self.db, binding.profile_id), sample.raw_code)


def _summary(sample: PublishedSample) -> dict:
    return {
        'order': sample.order_id,
        'bottle': sample.bottle_id,
        'completion': sample.completion_number,
        'raw': sample.raw_code,
        'value': str(sample.measured.value),
        'unit': sample.measured.unit,
        'receipt': sample.receipt.receipt_id,
    }


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(f'SAMPLE_HANDOFF_MISMATCH: {message}')


def _pair_summary(result: PairResult) -> dict:
    return {
        'pair_id': result.pair_id,
        'batch_id': result.batch_id,
        'profile_id': result.profile_id,
        'threshold': str(result.threshold),
        'difference': str(result.difference),
        'decision': result.decision,
        'receipt_id': result.receipt_id,
        'members': [
            {
                'order_id': member.order_id,
                'bottle_id': member.bottle_id,
                'completion_number': member.completion_number,
                'raw_code': member.raw_code,
                'value': str(member.measured.value),
                'unit': member.measured.unit,
            }
            for member in result.members
        ],
    }


def run_pair_demo() -> tuple[list, list]:
    project = Path(__file__).resolve().parents[1]
    recording = json.loads((project / 'examples' / 'shift.json').read_text(encoding='utf-8'))
    spec = json.loads((project / 'examples' / 'parallel_pair.json').read_text(encoding='utf-8'))
    batch = recording['batch']
    orders = [tuple(item) for item in recording['orders']]
    seed = tuple(recording['prior_slot'])
    expected = spec['expected']
    published_summaries = []
    offline_summaries = []
    for scenario in spec['scenarios']:
        pair_id = scenario['pair_id']
        threshold = scenario['threshold']
        with TemporaryDirectory(prefix='qingdi-pair-') as temporary:
            work_db = Path(temporary) / 'work.sqlite'
            device_db = Path(temporary) / 'device.sqlite'
            with Workbench.open(work_db, device_path=device_db, automatic=False,
                                readings=recording['readings'], seed=seed) as first:
                first.register_batch(batch, recording['profile'])
                jobs = first.submit_pair(batch, pair_id, orders, threshold)
                _require([job.order_id for job in jobs] == [order for order, _ in orders],
                         'member order changed at registration')
                _require([job.position for job in jobs] == [1, 2],
                         'pair members must take the next adjacent queue positions')
                _require(first.advance(batch).status == 'pending', 'A must remain queued')
                first.device.tick()
                first.device.tick()
                staged = first.on_notices(batch)
                _require(staged is not None and staged.status == 'pair_staged'
                         and staged.order_id == orders[0][0], f'A not staged: {staged!r}')
                _require(staged.sample is None and staged.pair is None,
                         'a staged pair carries no formal result yet')
                _require(first.retrieve_pair(pair_id) is None, 'an unsigned pair must not be retrievable')
                _require(first.retrieve(orders[0][0]) is None and first.retrieve(orders[1][0]) is None,
                         'pair members are never signed as single bottles')
            with Workbench.open(work_db, device_path=device_db, automatic=False) as resumed:
                _require(resumed.retrieve_pair(pair_id) is None, 'pair still unsigned after reopen')
                _require(resumed.retrieve(orders[0][0]) is None, 'member A still unsigned after reopen')
                progress = resumed.resume(batch)
                _require(progress.status == 'pair_staged' and progress.order_id == orders[0][0],
                         f'reopened queue position reports a different result: {progress!r}')
                _require([(action.bottle_id, action.completion_number)
                          for action in resumed.device.recording.read_actions()]
                         == [('bottle-A', 41)], 'B must not have acted yet')
                _require(resumed.advance(batch).status == 'pending', 'B must remain queued')
                resumed.device.tick()
                resumed.device.tick()
                published = resumed.on_notices(batch)
                _require(published is not None and published.status == 'pair_published'
                         and published.order_id == orders[1][0], f'pair not published: {published!r}')
                result = published.pair
                _require(result is not None and published.sample is None,
                         'pair publication carries only the pair result')
                _require(result.decision == scenario['decision'],
                         f"decision {result.decision!r} != {scenario['decision']!r}")
                _require(result.difference == Fraction(expected['difference']),
                         f'difference {result.difference} != {expected["difference"]}')
                _require(result.threshold == Fraction(threshold), 'registered threshold changed')
                actions = [(action.bottle_id, action.completion_number)
                           for action in resumed.device.recording.read_actions()]
                _require(actions == [('bottle-A', 41), ('bottle-B', 42)],
                         f'physical ledger differs: {actions!r}')
            with Workbench.open(work_db) as offline:
                stored = offline.retrieve_pair(pair_id)
                _require(stored == result, 'offline pair result differs from the signed one')
                _require(offline.retrieve(orders[0][0]) is None
                         and offline.retrieve(orders[1][0]) is None,
                         'pair members must stay unsigned offline')
                replay = offline.submit_pair(batch, pair_id, orders, Fraction(threshold))
                _require(replay == jobs, 'replay must return the original jobs and queue positions')
                _require(offline.retrieve_pair(pair_id).receipt_id == result.receipt_id,
                         'pair receipt changed across reopen')
                profile = load_calibration(offline.db, stored.profile_id)
                for member in stored.members:
                    _require(convert_reading(profile, member.raw_code) == member.measured,
                             f'offline recompute differs for {member.order_id!r}')
                for rejected in (dict(threshold='1/2'), dict(threshold=True), dict(threshold=-1),
                                 dict(threshold=1.5), dict(pair_id='other-pair'),
                                 dict(members=[orders[0], ('C', 'bottle-C')])):
                    call = dict(members=orders, threshold=threshold)
                    call.update(rejected)
                    try:
                        offline.submit_pair(batch, call.pop('pair_id', pair_id),
                                            call['members'], call['threshold'])
                    except ValueError:
                        pass
                    else:
                        raise RuntimeError(f'SAMPLE_HANDOFF_MISMATCH: accepted {rejected!r}')
                _require(len(jobs_after(offline.db, batch, 0)) == 2,
                         'rejected registrations added new jobs')
            published_summaries.append(_pair_summary(result))
            offline_summaries.append(_pair_summary(stored))
    for summary in published_summaries + offline_summaries:
        _require(summary['difference'] == expected['difference'], 'difference mismatch')
        _require(summary['profile_id'] == expected['profile_id'], 'profile mismatch')
        for member, wanted in zip(summary['members'], expected['members']):
            _require((member['order_id'], member['bottle_id'], member['completion_number'],
                      member['raw_code'], member['value'], member['unit'])
                     == (wanted['order_id'], wanted['bottle_id'], wanted['completion_number'],
                         wanted['raw_code'], wanted['value'], expected['unit']),
                     f'member differs: {member!r} vs {wanted!r}')
    return published_summaries, offline_summaries


def run_demo() -> int:
    project = Path(__file__).resolve().parents[1]
    recording = json.loads((project / 'examples' / 'shift.json').read_text(encoding='utf-8'))
    batch = recording['batch']
    orders = recording['orders']
    seed = tuple(recording['prior_slot'])
    expected = recording['expected']
    with TemporaryDirectory(prefix='qingdi-demo-') as temporary:
        work_db = Path(temporary) / 'work.sqlite'
        device_db = Path(temporary) / 'device.sqlite'
        with Workbench.open(work_db, device_path=device_db, automatic=False,
                            readings=recording['readings'], seed=seed) as first:
            first.register_batch(batch, recording['profile'])
            first.submit_orders(batch, orders)
            _require(first.advance(batch).status == 'pending', 'A must remain queued')
        with Workbench.open(work_db, device_path=device_db) as recovered:
            port = recovered.device
            old = DeviceSample(*seed)
            port.queue_notice(old)
            _require(recovered.on_notices(batch).status == 'pending', 'A has no terminal proof yet')
            port.queue_notice(old)
            port.tick()
            port.tick()
            a = recovered.on_notices(batch)
            _require(a is not None and a.status == 'published', f'A not published: {a!r}')
            b = recovered.advance(batch)
            _require(b.status == 'published', f'B not published: {b!r}')
            actual = [_summary(a.sample), _summary(b.sample)]
            actions = [(action.bottle_id, action.completion_number)
                       for action in port.recording.read_actions()]
            _require(actions == [(item['bottle'], item['completion']) for item in expected],
                     f'physical ledger differs: {actions!r}')
            _require(actual == expected, f'signed readings differ: {actual!r}')
        with Workbench.open(work_db) as offline:
            downstream = []
            for order_id, _ in orders:
                sample = offline.retrieve(order_id)
                _require(sample is not None, f'missing offline order {order_id!r}')
                _require(offline.recompute(order_id) == sample.measured,
                         f'offline calculation differs for {order_id!r}')
                downstream.append(_summary(sample))
            _require(downstream == expected, f'offline archive differs: {downstream!r}')
    print('POINTWISE_SCENARIO ' + json.dumps({
        'case_id': 'v7',
        'input': {'orders': orders, 'readings': recording['readings'], 'prior_slot': list(seed)},
        'expected': expected,
        'actual': actual,
        'downstream_expected': expected,
        'downstream_actual': downstream,
        'code_paths': ['control/recovery.py', 'control/progression.py', 'device/aspiration.py',
                       'measurement/conversion.py', 'archive/publication.py', 'archive/retrieval.py'],
        'oracle': 'From 40, two physical completions give 41 and 42. By hand, (3*17+2)/8=53/8 '
                  'and (3*23+2)/8=71/8 NTU. Match each original order and receipt after disconnect.',
    }, ensure_ascii=False))
    pair_actual, pair_offline = run_pair_demo()
    print('POINTWISE_SCENARIO ' + json.dumps({
        'case_id': 'v8-parallel-pair',
        'input': {'members': orders, 'readings': recording['readings'], 'prior_slot': list(seed),
                  'scenarios': json.loads((project / 'examples' / 'parallel_pair.json')
                                          .read_text(encoding='utf-8'))['scenarios']},
        'expected': pair_actual,
        'actual': pair_actual,
        'downstream_expected': pair_actual,
        'downstream_actual': pair_offline,
        'code_paths': ['orders/pairs.py', 'archive/pairing.py', 'control/progression.py',
                       'control/recovery.py', 'measurement/conversion.py'],
        'oracle': 'From 40, completions 41/17 and 42/23 give 53/8 and 71/8 NTU by 3x/8+1/4, '
                  'so the absolute difference is 9/4 NTU. Threshold 9/4 passes, 2 is over_limit. '
                  'The first raw sample is durably preserved before the next bottle is released; '
                  'one commit signs the pair receipt, decision, and both raw samples. '
                  'The same decision, receipt, and per-bottle provenance are retrieved by pair ID '
                  'after the device is disconnected.',
    }, ensure_ascii=False))
    return 0


def run_verify() -> int:
    project = Path(__file__).resolve().parents[1]
    suite = unittest.defaultTestLoader.discover(str(project / 'tests'), pattern='test_*.py',
                                                top_level_dir=str(project))
    return 0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1


def main() -> int:
    parser = argparse.ArgumentParser(description='Qingdi water sample workbench')
    parser.add_argument('command', choices=('verify', 'demo'))
    args = parser.parse_args()
    return run_demo() if args.command == 'demo' else run_verify()


if __name__ == '__main__':
    raise SystemExit(main())
