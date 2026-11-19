from measurement.profiles import build_profile
from orders.queue import find_job, jobs_after
from tests import BATCH, ORDERS, PROFILE, BenchCase


class AdmissionTests(BenchCase):
    def test_replay_and_conflict(self):
        bench = self.open_bench()
        (first,) = self.admit(bench, [ORDERS[0]])
        again = bench.submit_orders(BATCH, [ORDERS[0], ORDERS[0]])
        self.assertEqual(again, [first, first])
        with self.assertRaises(ValueError):
            bench.submit_orders(BATCH, [('A', 'bottle-B')])
        bench.register_batch('other', PROFILE)
        with self.assertRaises(ValueError):
            bench.submit_orders('other', [ORDERS[0]])
        self.assertEqual(jobs_after(bench.db, BATCH, 0), [first.job])
        self.assertEqual(jobs_after(bench.db, 'other', 0), [])

    def test_atomic_conflict(self):
        bench = self.open_bench()
        original = self.admit(bench)
        with self.assertRaises(ValueError):
            bench.submit_orders(BATCH, [('C', 'bottle-C'), ('A', 'bottle-B')])
        with self.assertRaises(ValueError):
            bench.submit_orders(BATCH, [('C', 'bottle-C'), ('C', 'bottle-A')])
        for invalid in [('D', '  '), (False, 'bottle-B'), ('D', 3), 'malformed']:
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    bench.submit_orders(BATCH, [('C', 'bottle-C'), invalid])
        bench.close()
        reopened = self.open_bench()
        self.assertIsNone(find_job(reopened.db, 'C'))
        self.assertEqual(jobs_after(reopened.db, BATCH, 0), [row.job for row in original])
        self.assertEqual(reopened.advance(BATCH).order_id, 'A')
        self.assertEqual(reopened.advance(BATCH).order_id, 'B')
        self.assertEqual(reopened.advance(BATCH).status, 'idle')
        self.assert_actions(reopened, [('bottle-A', 41), ('bottle-B', 42)])

    def test_replay_keeps_order(self):
        bench = self.open_bench()
        original = self.admit(bench)
        replay = bench.submit_orders(BATCH, [ORDERS[0]])
        self.assertEqual(replay, original[:1])
        self.assertEqual(bench.advance(BATCH).order_id, 'A')
        self.assertEqual(bench.advance(BATCH).order_id, 'B')
        self.assert_actions(bench, [('bottle-A', 41), ('bottle-B', 42)])

    def test_signed_replay(self):
        bench = self.open_bench()
        first, _ = self.admit(bench)
        signed = bench.advance(BATCH).sample
        self.assertIsNotNone(signed)
        bench.close()
        offline = self.open_bench(source=False)
        (replay,) = offline.submit_orders(BATCH, [ORDERS[0]])
        self.assertEqual(replay.job, first.job)
        self.assertEqual(replay.sample, signed)
        with self.assertRaises(ValueError):
            offline.submit_orders(BATCH, [('A', 'bottle-B')])
        self.assertEqual(offline.retrieve('A'), signed)

    def test_init_preserves_data(self):
        bench = self.open_bench()
        custom = build_profile('other-manual', 1, 30, '2/3', '-1/7', 'NTU')
        bench.install_calibration(custom)
        self.admit(bench)
        first = bench.advance(BATCH).sample
        self.assertIsNotNone(first)
        bench.close()
        reopened = self.open_bench()
        self.assertEqual(reopened.register_batch(BATCH, PROFILE).profile_id, PROFILE)
        self.assertEqual(reopened.install_calibration(custom), custom)
        with self.assertRaises(ValueError):
            reopened.install_calibration(build_profile('other-manual', 1, 30, 1, 0, 'NTU'))
        self.assertEqual(reopened.device.read_latest().completion_number, 41)
        self.assertEqual(reopened.retrieve('A'), first)
        self.assert_actions(reopened, [('bottle-A', 41)])
        self.assert_sample(reopened.advance(BATCH).sample, 'B', 'bottle-B', 42, 23, '71/8')
        reopened.register_batch('next-shift', 'other-manual')
        reopened.submit_orders('next-shift', [('C', 'bottle-C')])
        self.assert_sample(reopened.advance('next-shift').sample, 'C', 'bottle-C', 43, 8, '109/21')
