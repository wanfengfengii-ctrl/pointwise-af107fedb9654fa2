import json
import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class DeviceSample:
    completion_number: int
    raw_code: object


def write_completion(db: sqlite3.Connection, raw_code: object) -> DeviceSample:
    """Advance the physical counter and replace the single retained reading."""
    (current,) = db.execute('SELECT completion_number FROM latch WHERE slot = 1').fetchone()
    next_number = current + 1
    db.execute('UPDATE latch SET completion_number = ?, raw_json = ? WHERE slot = 1',
               (next_number, json.dumps(raw_code, allow_nan=False)))
    return DeviceSample(next_number, raw_code)


def read_latest(db: sqlite3.Connection) -> DeviceSample | None:
    row = db.execute('SELECT completion_number, raw_json FROM latch WHERE slot = 1').fetchone()
    if row is None or row[1] is None:
        return None
    return DeviceSample(row[0], json.loads(row[1]))
