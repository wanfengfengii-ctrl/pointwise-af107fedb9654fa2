import sqlite3
from dataclasses import dataclass

from measurement.calibration import load_calibration
from orders.bottles import validate_identifier


@dataclass(frozen=True)
class BatchBinding:
    batch_id: str
    profile_id: str


def initialize_batches(db: sqlite3.Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS batches (
            batch_id TEXT PRIMARY KEY NOT NULL,
            profile_id TEXT NOT NULL REFERENCES calibrations(profile_id)
        )
    ''')


def register_batch(db: sqlite3.Connection, batch_id: str, profile_id: str) -> BatchBinding:
    validate_identifier(batch_id, 'batch ID')
    with db:
        db.execute('BEGIN IMMEDIATE')
        load_calibration(db, profile_id)
        db.execute('INSERT INTO batches VALUES (?, ?) ON CONFLICT(batch_id) DO NOTHING',
                   (batch_id, profile_id))
        original = load_batch(db, batch_id)
        if original.profile_id != profile_id:
            raise ValueError(f'batch {batch_id!r} has a different fixed calibration')
    return original


def load_batch(db: sqlite3.Connection, batch_id: str) -> BatchBinding:
    validate_identifier(batch_id, 'batch ID')
    row = db.execute('SELECT batch_id, profile_id FROM batches WHERE batch_id = ?',
                     (batch_id,)).fetchone()
    if row is None:
        raise ValueError(f'unknown batch {batch_id!r}')
    return BatchBinding(*row)
