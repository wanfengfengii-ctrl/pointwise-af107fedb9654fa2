import sqlite3
from contextlib import closing
from fractions import Fraction

from control.active import load_active, load_progress
from device.latch import DeviceSample
from measurement.conversion import MeasuredValue, convert_reading
from measurement.calibration import load_calibration
from orders.pairs import find_pair_for_order
from orders.queue import jobs_after
from tests import BATCH, PROFILE, BenchCase

PAIR = 'pair-1'
MEMBERS = (('A', 'bottle-A'), ('B', 'bottle-B'))


class ParallelPairTests(BenchCase):
    def admit_pair(self, bench, threshold='9/4', pair_id=PAIR, batch=BATCH, members=MEMBERS):
        bench.register_batch(batch, PROFILE)
        return bench.submit_pair(batch, pair_id, members, threshold)

    def collect_pair(self, bench):
        first = bench.advance(BATCH)
        self.assertEqual(first.status, 'pair_staged')
        second = bench.advance(BATCH)
        self.assertEqual(second.status, 'pair_published')
        return second.pair

    def assert_pair(self, result, pair_id=PAIR, threshold=Fraction(9, 4),
                    difference=Fraction(9, 4), decision='pass'):
        self.assertIsNotNone(result)
        self.assertEqual((result.pair_id, result.batch_id, result.profile_id),
                         (pair_id, BATCH, PROFILE))
        self.assertEqual(result.threshold, threshold)
        self.assertEqual(result.difference, difference)
        self.assertEqual(result.decision, decision)
        self.assertIsInstance(result.receipt_id, str)
        self.assertTrue(result.receipt_id.strip())
        first, second = result.members
        self.assertEqual((first.order_id, first.bottle_id, first.completion_number, first.raw_code),
                         ('A', 'bottle-A', 41, 17))
        self.assertEqual(first.measured, MeasuredValue(Fraction(53, 8), 'NTU'))
        self.assertEqual((second.order_id, second.bottle_id, second.completion_number,
                          second.raw_code), ('B', 'bottle-B', 42, 23))
        self.assertEqual(second.measured, MeasuredValue(Fraction(71, 8), 'NTU'))

    def test_registration_replay_and_rejection(self):
        bench = self.open_bench()
        jobs = self.admit_pair(bench)
        self.assertEqual([(job.order_id, job.bottle_id, job.position) for job in jobs],
                         [('A', 'bottle-A', 1), ('B', 'bottle-B', 2)])
        for equal_threshold in ('9/4', Fraction(9, 4), '2.25', Fraction(18, 8)):
            with self.subTest(threshold=equal_threshold):
                self.assertEqual(bench.submit_pair(BATCH, PAIR, MEMBERS, equal_threshold), jobs)
        bench.register_batch('other', PROFILE)
        for conflicting in (
            (BATCH, PAIR, MEMBERS, '5/2'),
            (BATCH, PAIR, (MEMBERS[1], MEMBERS[0]), '9/4'),
            (BATCH, PAIR, (('A', 'bottle-A'), ('B', 'bottle-C')), '9/4'),
            ('other', PAIR, MEMBERS, '9/4'),
            ('missing-batch', PAIR, MEMBERS, '9/4'),
            (BATCH, 'pair-2', (('A', 'bottle-A'), ('C', 'bottle-C')), '9/4'),
            (BATCH, 'pair-2', (('C', 'bottle-C'), ('B', 'bottle-B')), '9/4'),
        ):
            with self.subTest(conflicting=conflicting):
                with self.assertRaises(ValueError):
                    bench.submit_pair(*conflicting)
        for invalid in (
            (' ', MEMBERS, '9/4'), (7, MEMBERS, '9/4'),
            (PAIR, MEMBERS[:1], '9/4'), (PAIR, MEMBERS + (('C', 'bottle-C'),), '9/4'),
            (PAIR, (('C', 'bottle-C'), ('C', 'bottle-D')), '9/4'),
            (PAIR, (('C', 'bottle-C'), ('D', 'bottle-C')), '9/4'),
            (PAIR, (('C', 'bottle-C'), 'malformed'), '9/4'),
            (PAIR, (('C', 'bottle-C'), ('D', 'bottle-D')), '-1/4'),
            (PAIR, (('C', 'bottle-C'), ('D', 'bottle-D')), True),
            (PAIR, (('C', 'bottle-C'), ('D', 'bottle-D')), 1.5),
            (PAIR, (('C', 'bottle-C'), ('D', 'bottle-D')), 'junk'),
            (PAIR, (('C', 'bottle-C'), ('D', 'bottle-D')), None),
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    bench.submit_pair(BATCH, *invalid)
        self.assertEqual(jobs_after(bench.db, BATCH, 0), list(jobs))
        self.assertEqual(jobs_after(bench.db, 'other', 0), [])
        self.assertEqual(bench.db.execute('SELECT COUNT(*) FROM pairs').fetchone(), (1,))
        bench.submit_orders(BATCH, [('C', 'bottle-C')])
        with self.assertRaises(ValueError):
            bench.submit_pair(BATCH, 'pair-2', (('C', 'bottle-C'), ('D', 'bottle-D')), '9/4')
        (replay,) = bench.submit_orders(BATCH, [MEMBERS[0]])
        self.assertEqual(replay.job, jobs[0])
        self.assertIsNone(replay.sample)
        self.assertEqual(find_pair_for_order(bench.db, 'A').pair_id, PAIR)
        result = self.collect_pair(bench)
        self.assert_pair(result)
        self.assertEqual(bench.submit_pair(BATCH, PAIR, MEMBERS, '2.25'), jobs)
        self.assertEqual(bench.retrieve_pair(PAIR), result)
        self.assert_actions(bench, [('bottle-A', 41), ('bottle-B', 42)])

    def test_invalid_reading_keeps_pair_member_active(self):
        bench = self.open_bench(readings={'bottle-A': 32, 'bottle-B': 23, 'bottle-C': 8})
        self.admit_pair(bench)
        rejected = bench.advance(BATCH)
        self.assertEqual(rejected.status, 'invalid_reading')
        self.assertEqual(rejected.order_id, 'A')
        self.assertEqual(load_active(bench.db).completion_number, 41)
        self.assertEqual(load_progress(bench.db, BATCH), 0)
        self.assertIsNone(bench.retrieve_pair(PAIR))
        self.assertIsNone(bench.retrieve('A'))
        self.assertEqual(bench.db.execute('SELECT COUNT(*) FROM pair_evidence').fetchone(), (0,))
        self.assertEqual(bench.db.execute('SELECT COUNT(*) FROM pair_results').fetchone(), (0,))
        self.assertEqual(bench.resume(BATCH).status, 'invalid_reading')
        self.assert_actions(bench, [('bottle-A', 41)])

    def test_staged_reopen_then_publish(self):
        bench = self.open_bench(automatic=False)
        self.admit_pair(bench)
        self.assertEqual(bench.advance(BATCH).status, 'pending')
        bench.device.tick()
        bench.device.tick()
        staged = bench.on_notices(BATCH)
        self.assertEqual(staged.status, 'pair_staged')
        self.assertEqual(staged.order_id, 'A')
        self.assertIsNone(staged.sample)
        self.assertIsNone(staged.pair)
        self.assertIsNone(bench.retrieve_pair(PAIR))
        self.assertIsNone(bench.retrieve('A'))
        self.assertIsNone(bench.recompute('A'))
        self.assertEqual(bench.device.read_latest(), DeviceSample(41, 17))
        bench.close()
        resumed = self.open_bench(automatic=False)
        self.assertIsNone(resumed.retrieve_pair(PAIR))
        self.assertIsNone(resumed.retrieve('A'))
        again = resumed.resume(BATCH)
        self.assertEqual(again.status, 'pair_staged')
        self.assertEqual(again.order_id, 'A')
        self.assert_actions(resumed, [('bottle-A', 41)])
        self.assertEqual(resumed.advance(BATCH).status, 'pending')
        resumed.device.tick()
        resumed.device.tick()
        published = resumed.on_notices(BATCH)
        self.assertEqual(published.status, 'pair_published')
        self.assertEqual(published.order_id, 'B')
        self.assertIsNone(published.sample)
        self.assert_pair(published.pair)
        self.assertIsNone(resumed.retrieve('A'))
        self.assertIsNone(resumed.retrieve('B'))
        self.assertIsNone(resumed.recompute('B'))
        followup = resumed.resume(BATCH)
        self.assertEqual(followup.status, 'pair_published')
        self.assertEqual(followup.pair, published.pair)
        self.assertEqual(resumed.advance(BATCH).status, 'idle')
        resumed.close()
        offline = self.open_bench(source=False)
        stored = offline.retrieve_pair(PAIR)
        self.assertEqual(stored, published.pair)
        self.assertEqual(offline.retrieve_pair(PAIR).receipt_id, published.pair.receipt_id)
        profile = load_calibration(offline.db, stored.profile_id)
        for member in stored.members:
            self.assertEqual(convert_reading(profile, member.raw_code), member.measured)
        self.assertIsNone(offline.retrieve_pair('unknown-pair'))
        for illegal in (' ', '', 3, None):
            with self.subTest(illegal=illegal):
                with self.assertRaises(ValueError):
                    offline.retrieve_pair(illegal)

    def test_over_limit_keeps_both_raw_samples(self):
        bench = self.open_bench()
        self.admit_pair(bench, threshold='2')
        result = self.collect_pair(bench)
        self.assert_pair(result, threshold=Fraction(2), decision='over_limit')
        bench.close()
        offline = self.open_bench(source=False)
        stored = offline.retrieve_pair(PAIR)
        self.assertEqual(stored, result)
        self.assertEqual([member.raw_code for member in stored.members], [17, 23])
        self.assertEqual([member.completion_number for member in stored.members], [41, 42])

    def test_first_member_commit_failure_blocks_pair(self):
        bench = self.open_bench()
        self.admit_pair(bench)
        bench.register_batch('other', PROFILE)
        bench.submit_orders('other', [('C', 'bottle-C')])
        bench.db.executescript('''
            CREATE TABLE allowed_pair_evidence (order_id TEXT PRIMARY KEY);
            CREATE TABLE pair_evidence_constraint (
                order_id TEXT REFERENCES allowed_pair_evidence(order_id) DEFERRABLE INITIALLY DEFERRED
            );
            CREATE TRIGGER reject_pair_preservation AFTER INSERT ON pair_evidence
            WHEN NEW.order_id = 'A'
            BEGIN
                INSERT INTO pair_evidence_constraint VALUES (NEW.order_id);
            END;
        ''')
        rejected = bench.advance(BATCH)
        self.assertEqual(rejected.status, 'commit_failed')
        self.assertIn('FOREIGN KEY constraint failed', rejected.detail)
        self.assertFalse(bench.db.in_transaction)
        self.assertIsNone(bench.retrieve_pair(PAIR))
        self.assertIsNone(bench.retrieve('A'))
        self.assertEqual(bench.device.read_latest(), DeviceSample(41, 17))
        self.assertEqual(load_progress(bench.db, BATCH), 0)
        self.assertEqual(load_active(bench.db).completion_number, 41)
        self.assertEqual(bench.advance('other').status, 'pending')
        self.assertEqual(bench.advance(BATCH).status, 'commit_failed')
        self.assert_actions(bench, [('bottle-A', 41)])
        self.assertEqual(
            bench.device.recording.db.execute('SELECT COUNT(*) FROM commands').fetchone(), (1,))
        bench.db.execute('DROP TRIGGER reject_pair_preservation')
        bench.close()
        resumed = self.open_bench()
        staged = resumed.resume(BATCH)
        self.assertEqual(staged.status, 'pair_staged')
        self.assertEqual(staged.order_id, 'A')
        self.assert_actions(resumed, [('bottle-A', 41)])
        published = resumed.advance(BATCH)
        self.assertEqual(published.status, 'pair_published')
        self.assert_pair(published.pair)
        self.assert_actions(resumed, [('bottle-A', 41), ('bottle-B', 42)])

    def test_last_member_signing_failure_blocks_release(self):
        bench = self.open_bench()
        self.admit_pair(bench)
        bench.register_batch('other', PROFILE)
        bench.submit_orders('other', [('C', 'bottle-C')])
        staged = bench.advance(BATCH)
        self.assertEqual(staged.status, 'pair_staged')
        bench.db.executescript('''
            CREATE TABLE allowed_pair_results (pair_id TEXT PRIMARY KEY);
            CREATE TABLE pair_result_constraint (
                pair_id TEXT REFERENCES allowed_pair_results(pair_id) DEFERRABLE INITIALLY DEFERRED
            );
            CREATE TRIGGER reject_pair_signing AFTER INSERT ON pair_results
            BEGIN
                INSERT INTO pair_result_constraint VALUES (NEW.pair_id);
            END;
        ''')
        rejected = bench.advance(BATCH)
        self.assertEqual(rejected.status, 'commit_failed')
        self.assertIn('FOREIGN KEY constraint failed', rejected.detail)
        self.assertFalse(bench.db.in_transaction)
        self.assertIsNone(bench.retrieve_pair(PAIR))
        self.assertEqual(load_progress(bench.db, BATCH), 1)
        self.assertEqual(load_active(bench.db).completion_number, 42)
        self.assertEqual(bench.device.read_latest(), DeviceSample(42, 23))
        self.assertEqual(bench.advance('other').status, 'pending')
        self.assert_actions(bench, [('bottle-A', 41), ('bottle-B', 42)])
        bench.db.execute('DROP TRIGGER reject_pair_signing')
        bench.close()
        resumed = self.open_bench()
        published = resumed.resume(BATCH)
        self.assertEqual(published.status, 'pair_published')
        self.assert_pair(published.pair)
        self.assert_actions(resumed, [('bottle-A', 41), ('bottle-B', 42)])
        self.assertEqual(
            resumed.device.recording.db.execute('SELECT COUNT(*) FROM commands').fetchone(), (2,))
        resumed.close()
        offline = self.open_bench(source=False)
        self.assertEqual(offline.retrieve_pair(PAIR), published.pair)

    def test_pair_checkpoint_failure_recovers_from_committed_evidence(self):
        bench = self.open_bench()
        self.admit_pair(bench)
        bench.db.executescript('''
            CREATE TRIGGER reject_pair_checkpoint BEFORE INSERT ON control_progress
            BEGIN
                SELECT RAISE(ABORT, 'checkpoint unavailable');
            END;
        ''')
        step = bench.advance(BATCH)
        self.assertEqual(step.status, 'checkpoint_failed')
        self.assertEqual(load_progress(bench.db, BATCH), 0)
        self.assertEqual(load_active(bench.db).completion_number, 41)
        self.assertIsNone(bench.retrieve_pair(PAIR))
        self.assert_actions(bench, [('bottle-A', 41)])
        bench.db.execute('DROP TRIGGER reject_pair_checkpoint')
        bench.close()
        resumed = self.open_bench()
        resumed.device.reads_available = False
        staged = resumed.resume(BATCH)
        self.assertEqual(staged.status, 'pair_staged')
        self.assertEqual(load_progress(resumed.db, BATCH), 1)
        resumed.device.reads_available = True
        resumed.db.executescript('''
            CREATE TRIGGER reject_pair_update BEFORE UPDATE ON control_progress
            BEGIN
                SELECT RAISE(ABORT, 'checkpoint unavailable');
            END;
        ''')
        blocked = resumed.advance(BATCH)
        self.assertEqual(blocked.status, 'checkpoint_failed')
        self.assertEqual(load_progress(resumed.db, BATCH), 1)
        self.assertEqual(load_active(resumed.db).completion_number, 42)
        self.assert_pair(resumed.retrieve_pair(PAIR))
        resumed.db.execute('DROP TRIGGER reject_pair_update')
        resumed.close()
        recovered = self.open_bench()
        published = recovered.resume(BATCH)
        self.assertEqual(published.status, 'pair_published')
        self.assert_pair(published.pair)
        self.assert_actions(recovered, [('bottle-A', 41), ('bottle-B', 42)])
