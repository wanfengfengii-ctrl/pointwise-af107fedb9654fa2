import argparse
import json
import sqlite3
import unittest
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

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
from orders.bottles import BottleJob
from orders.queue import initialize_queue


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
            initialize_active(db)
            ArchiveStore(db).initialize()
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
