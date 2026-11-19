from tests import BATCH, BenchCase


class RetrievalTests(BenchCase):
    def test_offline_after_next_sample(self):
        bench = self.open_bench()
        self.admit(bench)
        a = bench.advance(BATCH).sample
        b = bench.advance(BATCH).sample
        self.assertIsNotNone(a)
        self.assertIsNotNone(b)
        self.assertEqual(bench.device.read_latest().completion_number, 42)
        bench.close()
        offline = self.open_bench(source=False)
        self.assertIsNone(offline.device)
        self.assertEqual(offline.retrieve('B'), b)
        self.assertEqual(offline.retrieve('A'), a)
        self.assert_sample(offline.retrieve('A'), 'A', 'bottle-A', 41, 17, '53/8')
        self.assert_sample(offline.retrieve('B'), 'B', 'bottle-B', 42, 23, '71/8')
        self.assertEqual(offline.recompute('A'), a.measured)
        self.assertEqual(offline.recompute('B'), b.measured)
        self.assertIsNone(offline.retrieve('unknown'))

    def test_receipt_pairing(self):
        bench = self.open_bench()
        self.admit(bench)
        first = bench.advance(BATCH).sample
        self.assertIsNotNone(first)
        original_receipt = bench.retrieve('A').receipt
        second = bench.advance(BATCH).sample
        self.assertIsNotNone(second)
        self.assertNotEqual(second.receipt, original_receipt)
        self.assertEqual(bench.retrieve('A').receipt, original_receipt)
        bench.close()
        offline = self.open_bench(source=False)
        for order_id, receipt in [('B', second.receipt), ('A', original_receipt), ('B', second.receipt)]:
            with self.subTest(order_id=order_id):
                self.assertEqual(offline.retrieve(order_id).receipt, receipt)
