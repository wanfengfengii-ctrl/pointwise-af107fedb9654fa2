import sqlite3
from collections.abc import Iterable

from orders.bottles import BottleJob


_JOB_COLUMNS = 'order_id, batch_id, bottle_id, position'


def initialize_queue(db: sqlite3.Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS jobs (
            order_id TEXT PRIMARY KEY NOT NULL,
            batch_id TEXT NOT NULL REFERENCES batches(batch_id),
            bottle_id TEXT NOT NULL,
            position INTEGER NOT NULL CHECK (position > 0),
            UNIQUE(batch_id, position)
        )
    ''')


def find_job(db: sqlite3.Connection, order_id: str) -> BottleJob | None:
    row = db.execute(f'SELECT {_JOB_COLUMNS} FROM jobs WHERE order_id = ?',
                     (order_id,)).fetchone()
    return None if row is None else BottleJob(*row)


def append_jobs(db: sqlite3.Connection, batch_id: str,
                items: Iterable[tuple[str, str]]) -> list[BottleJob]:
    """Append previously unseen orders in the caller's admission transaction."""
    (tail,) = db.execute('SELECT COALESCE(MAX(position), 0) FROM jobs WHERE batch_id = ?',
                         (batch_id,)).fetchone()
    jobs = []
    for position, (order_id, bottle_id) in enumerate(items, tail + 1):
        db.execute('INSERT INTO jobs VALUES (?, ?, ?, ?)',
                   (order_id, batch_id, bottle_id, position))
        jobs.append(BottleJob(order_id, batch_id, bottle_id, position))
    return jobs


def jobs_after(db: sqlite3.Connection, batch_id: str, position: int,
               limit: int = -1) -> list[BottleJob]:
    rows = db.execute(f'''
        SELECT {_JOB_COLUMNS} FROM jobs WHERE batch_id = ? AND position > ?
        ORDER BY position LIMIT ?
    ''', (batch_id, position, limit))
    return [BottleJob(*row) for row in rows]
