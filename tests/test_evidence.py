from control.active import load_active
from device.latch import DeviceSample
from tests import BATCH, BenchCase


class EvidenceTests(BenchCase):
    def test_old_notice_during_resume(self):
        bench = self.open_bench(automatic=False)
        self.admit(bench)
        self.assertEqual(bench.advance(BATCH).status, 'pending')
        bench.close()
        recovered = self.open_bench()
        port = recovered.device
        old = DeviceSample(40, 12)
        port.queue_notice(old)
        pending = recovered.on_notices(BATCH)
        self.assertEqual(pending.status, 'pending')
        self.assertIsNone(recovered.retrieve('A'))
        port.queue_notice(old)
        port.queue_notice(old)
        port.tick()
        port.tick()
        port.queue_notice(old)
        result = recovered.on_notices(BATCH)
        self.assertEqual(result.status, 'published')
        self.assert_sample(result.sample, 'A', 'bottle-A', 41, 17, '53/8')
        self.assert_actions(recovered, [('bottle-A', 41)])
        second = recovered.advance(BATCH)
        self.assertEqual(second.status, 'published')
        self.assert_sample(second.sample, 'B', 'bottle-B', 42, 23, '71/8')
        self.assertEqual(recovered.retrieve('A'), result.sample)
        self.assert_actions(recovered, [('bottle-A', 41), ('bottle-B', 42)])

    def test_duplicate_after_commit(self):
        bench = self.open_bench()
        self.admit(bench)
        first = bench.advance(BATCH).sample
        self.assertIsNotNone(first)
        for number, code in ((40, 12), (41, 17), (40, 12), (41, 17)):
            bench.device.queue_notice(DeviceSample(number, code))
        self.assertEqual(bench.on_notices(BATCH).sample, first)
        self.assertIsNone(bench.on_notices(BATCH))
        self.assertIsNone(bench.retrieve('B'))
        self.assert_actions(bench, [('bottle-A', 41)])
        second = bench.advance(BATCH).sample
        self.assert_sample(second, 'B', 'bottle-B', 42, 23, '71/8')
        self.assertEqual(bench.retrieve('A'), first)

    def test_source_read_failure_keeps_completion(self):
        bench = self.open_bench()
        self.admit(bench)
        bench.device.reads_available = False
        self.assertEqual(bench.advance(BATCH).status, 'source_read_failed')
        self.assertEqual(load_active(bench.db).completion_number, 41)
        self.assertIsNone(bench.retrieve('A'))
        self.assert_actions(bench, [('bottle-A', 41)])
        bench.device.reads_available = True
        self.assert_sample(bench.resume(BATCH).sample, 'A', 'bottle-A', 41, 17, '53/8')

    def test_slot_mismatch_does_not_substitute_a_reading(self):
        bench = self.open_bench(automatic=False)
        self.admit(bench)
        bench.advance(BATCH)
        bench.device.tick()
        bench.device.tick()
        # A manual device operation outside the workbench consumes the retained slot.
        external = bench.device.open_session()
        bench.device.submit(external, 'manual-check', 'bottle-C')
        bench.device.tick()
        bench.device.tick()
        self.assertEqual(bench.device.read_latest(), DeviceSample(42, 8))
        self.assertEqual(bench.resume(BATCH).status, 'source_read_failed')
        self.assertEqual(load_active(bench.db).completion_number, 41)
        self.assertIsNone(bench.retrieve('A'))
        self.assertIsNone(bench.retrieve('B'))
        self.assert_actions(bench, [('bottle-A', 41), ('bottle-C', 42)])
