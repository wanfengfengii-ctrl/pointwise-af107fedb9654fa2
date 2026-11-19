from fractions import Fraction

from measurement.profiles import build_profile
from tests import BATCH, BenchCase


class CollectionTests(BenchCase):
    def test_two_bottles_in_order(self):
        bench = self.open_bench()
        admissions = self.admit(bench)
        self.assertEqual([(row.job.order_id, row.job.position) for row in admissions], [('A', 1), ('B', 2)])
        first = bench.advance(BATCH)
        self.assertEqual(first.status, 'published')
        self.assert_sample(first.sample, 'A', 'bottle-A', 41, 17, '53/8')
        self.assertIsNone(bench.retrieve('B'))
        self.assert_actions(bench, [('bottle-A', 41)])
        second = bench.advance(BATCH)
        self.assertEqual(second.status, 'published')
        self.assert_sample(second.sample, 'B', 'bottle-B', 42, 23, '71/8')
        self.assert_actions(bench, [('bottle-A', 41), ('bottle-B', 42)])
        self.assertEqual(bench.advance(BATCH).status, 'idle')
        self.assertEqual(bench.resume(BATCH).sample, second.sample)

    def test_bound_calibration(self):
        bench = self.open_bench()
        self.admit(bench)
        first = bench.advance(BATCH)
        self.assertEqual(first.status, 'published')
        new_profile = build_profile('ntu-v2', 0, 31, '1/2', 0, 'NTU')
        self.assertEqual(bench.install_calibration(new_profile), new_profile)
        with self.assertRaises(ValueError):
            bench.register_batch(BATCH, 'ntu-v2')
        with self.assertRaises(ValueError):
            bench.register_batch('unknown-profile', 'missing')
        second = bench.advance(BATCH)
        self.assertEqual(second.status, 'published')
        self.assert_sample(second.sample, 'B', 'bottle-B', 42, 23, '71/8')
        bench.register_batch('next-shift', 'ntu-v2')
        bench.submit_orders('next-shift', [('C', 'bottle-B')])
        third = bench.advance('next-shift')
        self.assertEqual(third.status, 'published')
        self.assert_sample(third.sample, 'C', 'bottle-B', 43, 23, '23/2')
        self.assertEqual(bench.retrieve('A'), first.sample)
        self.assertEqual(bench.recompute('A').value, Fraction(53, 8))

    def test_exact_fraction(self):
        bench = self.open_bench()
        self.admit(bench)
        bench.advance(BATCH)
        bench.advance(BATCH)
        values = [bench.retrieve(order_id).measured.value for order_id in ('A', 'B')]
        self.assertEqual([(value.numerator, value.denominator) for value in values], [(53, 8), (71, 8)])
        self.assertEqual(values, [Fraction(53, 8), Fraction(71, 8)])
