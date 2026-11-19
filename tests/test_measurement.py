from fractions import Fraction

from measurement.conversion import convert_reading
from measurement.limits import InvalidReading
from measurement.profiles import build_profile
from orders.batches import load_batch
from tests import BATCH, PROFILE, BenchCase


class MeasurementTests(BenchCase):
    def test_invalid_reading_has_no_receipt(self):
        bench = self.open_bench(readings={'bottle-A': 17, 'bottle-B': 32, 'bottle-C': 8})
        self.admit(bench, [('A', 'bottle-A'), ('B', 'bottle-B'), ('C', 'bottle-C')])
        first = bench.advance(BATCH).sample
        self.assert_sample(first, 'A', 'bottle-A', 41, 17, '53/8')
        rejected = bench.advance(BATCH)
        self.assertEqual(rejected.status, 'invalid_reading')
        self.assertEqual(rejected.order_id, 'B')
        self.assertIsNone(bench.retrieve('B'))
        self.assertIsNone(bench.retrieve('C'))
        self.assertEqual(bench.db.execute('SELECT order_id FROM receipts').fetchall(), [('A',)])
        self.assertEqual(bench.resume(BATCH).status, 'invalid_reading')
        self.assert_actions(bench, [('bottle-A', 41), ('bottle-B', 42)])
        self.assertEqual(load_batch(bench.db, BATCH).profile_id, PROFILE)
        self.assertEqual(bench.retrieve('A'), first)

    def test_raw_range_and_type(self):
        profile = build_profile(PROFILE, 0, 31, '3/8', '1/4', 'NTU')
        for raw in (None, False, True, 17.0, '17', [], -1, 32):
            with self.subTest(raw=raw):
                with self.assertRaises(InvalidReading):
                    convert_reading(profile, raw)
        self.assertEqual(convert_reading(profile, 0).value, Fraction(1, 4))
        self.assertEqual(convert_reading(profile, 31).value, Fraction(95, 8))
        bench = self.open_bench(readings={'bottle-A': True, 'bottle-B': 23})
        self.admit(bench)
        self.assertEqual(bench.advance(BATCH).status, 'invalid_reading')
        self.assertIs(bench.device.read_latest().raw_code, True)
        self.assertIsNone(bench.retrieve('A'))
        self.assert_actions(bench, [('bottle-A', 41)])
