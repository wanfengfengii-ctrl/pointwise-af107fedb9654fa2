import sqlite3
from dataclasses import dataclass

from orders.bottles import BottleJob


@dataclass(frozen=True)
class ActiveCollection:
    job: BottleJob
    session_id: int
    command_id: str
    completion_number: int | None = None


def initialize_active(db: sqlite3.Connection) -> None:
    db.executescript('''
        CREATE TABLE IF NOT EXISTS active_collection (
            slot INTEGER PRIMARY KEY CHECK (slot = 1),
            order_id TEXT NOT NULL REFERENCES jobs(order_id),
            session_id INTEGER NOT NULL,
            command_id TEXT NOT NULL,
            completion_number INTEGER
        );
        CREATE TABLE IF NOT EXISTS control_progress (
            batch_id TEXT PRIMARY KEY NOT NULL REFERENCES batches(batch_id),
            position INTEGER NOT NULL CHECK (position > 0)
        );
    ''')


def load_active(db: sqlite3.Connection) -> ActiveCollection | None:
    row = db.execute('''
        SELECT j.order_id, j.batch_id, j.bottle_id, j.position,
               a.session_id, a.command_id, a.completion_number
        FROM active_collection AS a JOIN jobs AS j USING (order_id) WHERE a.slot = 1
    ''').fetchone()
    if row is None:
        return None
    return ActiveCollection(BottleJob(*row[:4]), *row[4:])


def load_progress(db: sqlite3.Connection, batch_id: str) -> int:
    row = db.execute('SELECT position FROM control_progress WHERE batch_id = ?', (batch_id,)).fetchone()
    return 0 if row is None else row[0]


def reserve_attempt(db: sqlite3.Connection, job: BottleJob, session_id: int, command_id: str,
                    prior: ActiveCollection | None = None) -> ActiveCollection | None:
    with db:
        db.execute('BEGIN IMMEDIATE')
        current = load_active(db)
        if current != prior or job.position != load_progress(db, job.batch_id) + 1:
            return None
        if prior is None:
            db.execute('INSERT INTO active_collection VALUES (1, ?, ?, ?, NULL)',
                       (job.order_id, session_id, command_id))
        else:
            if prior.completion_number is not None or prior.job != job:
                return None
            db.execute('UPDATE active_collection SET session_id = ?, command_id = ? WHERE slot = 1',
                       (session_id, command_id))
    return ActiveCollection(job, session_id, command_id)


def record_completion(db: sqlite3.Connection, attempt: ActiveCollection,
                      number: int) -> ActiveCollection | None:
    with db:
        db.execute('BEGIN IMMEDIATE')
        current = load_active(db)
        if current is None or (current.job, current.session_id, current.command_id) != (
            attempt.job, attempt.session_id, attempt.command_id
        ):
            return None
        if current.completion_number not in (None, number):
            return None
        db.execute('UPDATE active_collection SET completion_number = ? WHERE slot = 1', (number,))
    return ActiveCollection(attempt.job, attempt.session_id, attempt.command_id, number)


def confirm_delivery(db: sqlite3.Connection, attempt: ActiveCollection,
                     published_completion: int) -> bool:
    if attempt.completion_number != published_completion:
        return False
    job = attempt.job
    with db:
        db.execute('BEGIN IMMEDIATE')
        progress = load_progress(db, job.batch_id)
        if progress >= job.position:
            return True
        if load_active(db) != attempt or progress + 1 != job.position:
            return False
        db.execute('''
            INSERT INTO control_progress VALUES (?, ?)
            ON CONFLICT(batch_id) DO UPDATE SET position = excluded.position
        ''', (job.batch_id, job.position))
        db.execute('DELETE FROM active_collection WHERE slot = 1')
    return True
