import unittest
from fractions import Fraction
from pathlib import Path
from tempfile import TemporaryDirectory

from control.workbench import Workbench
from measurement.conversion import MeasuredValue


PROFILE = 'ntu-v1'
BATCH = 'shift'
ORDERS = (('A', 'bottle-A'), ('B', 'bottle-B'))
READINGS = {'bottle-A': 17, 'bottle-B': 23, 'bottle-C': 8}


class BenchCase(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory(prefix='qingdi-tests-')
        self.addCleanup(folder.cleanup)
        self.work_path = Path(folder.name) / 'work.sqlite'
        self.device_path = Path(folder.name) / 'device.sqlite'

    def open_bench(self, *, automatic=True, source=True, readings=None):
        if source:
            initializing = not self.device_path.exists()
            if initializing and readings is None:
                readings = READINGS
            bench = Workbench.open(self.work_path, device_path=self.device_path,
                                   readings=readings, seed=(40, 12) if initializing else None,
                                   automatic=automatic)
        else:
            bench = Workbench.open(self.work_path)
        self.addCleanup(bench.close)
        return bench

    def admit(self, bench, orders=ORDERS, batch=BATCH):
        bench.register_batch(batch, PROFILE)
        return bench.submit_orders(batch, orders)

    def assert_sample(self, sample, order_id, bottle_id, number, raw, value, unit='NTU'):
        self.assertIsNotNone(sample)
        self.assertEqual((sample.order_id, sample.bottle_id), (order_id, bottle_id))
        self.assertEqual((sample.completion_number, sample.raw_code), (number, raw))
        self.assertEqual(sample.measured, MeasuredValue(Fraction(value), unit))
        self.assertEqual((sample.receipt.receipt_id, sample.receipt.order_id,
                          sample.receipt.completion_number), (f'{number}/{order_id}', order_id, number))

    def assert_actions(self, bench, expected):
        actions = bench.device.recording.read_actions()
        self.assertEqual([(action.bottle_id, action.completion_number) for action in actions], expected)
