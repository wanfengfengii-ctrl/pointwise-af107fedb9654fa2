import sqlite3
from contextlib import closing

from control.active import load_active, load_progress
from device.latch import DeviceSample
from tests import BATCH, PROFILE, BenchCase


class PublicationTests(BenchCase):
    def test_commit_failure_blocks_next_bottle(self):
        bench = self.open_bench()
        self.admit(bench)
        bench.register_batch('other', PROFILE)
        bench.submit_orders('other', [('C', 'bottle-C')])
        bench.db.executescript('''
            CREATE TABLE allowed_receipts (order_id TEXT PRIMARY KEY);
            CREATE TABLE receipt_constraint (
                order_id TEXT REFERENCES allowed_receipts(order_id) DEFERRABLE INITIALLY DEFERRED
            );
            CREATE TRIGGER reject_a_at_commit AFTER INSERT ON receipts WHEN NEW.order_id = 'A'
            BEGIN
                INSERT INTO receipt_constraint VALUES (NEW.order_id);
            END;
        ''')
        rejected = bench.advance(BATCH)
        self.assertEqual(rejected.status, 'commit_failed')
        self.assertIn('FOREIGN KEY constraint failed', rejected.detail)
        self.assertFalse(bench.db.in_transaction)
        self.assertIsNone(bench.retrieve('A'))
        self.assertIsNone(bench.retrieve('B'))
        with closing(sqlite3.connect(self.work_path)) as reader:
            self.assertEqual(reader.execute('SELECT COUNT(*) FROM samples').fetchone(), (0,))
            self.assertEqual(reader.execute('SELECT COUNT(*) FROM receipts').fetchone(), (0,))
            self.assertEqual(reader.execute('SELECT COUNT(*) FROM receipt_constraint').fetchone(), (0,))
        self.assertEqual(load_progress(bench.db, BATCH), 0)
        self.assertEqual(load_active(bench.db).completion_number, 41)
        self.assertEqual(bench.advance('other').status, 'pending')
        self.assertEqual(bench.advance(BATCH).status, 'commit_failed')
        self.assert_actions(bench, [('bottle-A', 41)])
        self.assertEqual(bench.device.recording.db.execute('SELECT COUNT(*) FROM commands').fetchone(), (1,))
        self.assertEqual(bench.device.read_latest(), DeviceSample(41, 17))
        bench.db.execute('DROP TRIGGER reject_a_at_commit')
        bench.close()
        restarted = self.open_bench()
        recovered = restarted.resume(BATCH)
        self.assertEqual(recovered.status, 'published')
        self.assert_sample(recovered.sample, 'A', 'bottle-A', 41, 17, '53/8')
        self.assert_actions(restarted, [('bottle-A', 41)])
        self.assert_sample(restarted.advance(BATCH).sample, 'B', 'bottle-B', 42, 23, '71/8')
        with closing(sqlite3.connect(self.work_path)) as reader:
            self.assertEqual(reader.execute('SELECT order_id, completion_number FROM samples ORDER BY order_id').fetchall(),
                             [('A', 41), ('B', 42)])
            self.assertEqual(reader.execute('SELECT order_id, completion_number FROM receipts ORDER BY order_id').fetchall(),
                             [('A', 41), ('B', 42)])

    def test_commit_before_checkpoint(self):
        bench = self.open_bench()
        self.admit(bench)
        bench.db.executescript('''
            CREATE TRIGGER reject_checkpoint BEFORE INSERT ON control_progress
            BEGIN
                SELECT RAISE(ABORT, 'checkpoint unavailable');
            END;
        ''')
        step = bench.advance(BATCH)
        self.assertEqual(step.status, 'checkpoint_failed')
        self.assertEqual(load_progress(bench.db, BATCH), 0)
        self.assertEqual(load_active(bench.db).completion_number, 41)
        self.assert_actions(bench, [('bottle-A', 41)])
        reader = self.open_bench(source=False)
        published = reader.retrieve('A')
        self.assert_sample(published, 'A', 'bottle-A', 41, 17, '53/8')
        self.assertIsNone(reader.retrieve('B'))
        reader.close()
        bench.db.execute('DROP TRIGGER reject_checkpoint')
        bench.close()
        reopened = self.open_bench()
        reopened.device.reads_available = False
        resumed = reopened.resume(BATCH)
        self.assertEqual(resumed.status, 'published')
        self.assertEqual(resumed.sample, published)
        self.assertIsNone(load_active(reopened.db))
        self.assertEqual(load_progress(reopened.db, BATCH), 1)
        self.assert_actions(reopened, [('bottle-A', 41)])
        reopened.device.reads_available = True
        self.assert_sample(reopened.advance(BATCH).sample, 'B', 'bottle-B', 42, 23, '71/8')
