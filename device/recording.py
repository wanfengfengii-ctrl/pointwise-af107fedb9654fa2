import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path


class DeviceError(RuntimeError):
    """The device could not accept or supply the requested operation."""


@dataclass(frozen=True)
class RecordedAction:
    session_id: int
    command_id: str
    bottle_id: str
    completion_number: int | None


class DeviceRecording:
    def __init__(self, path: str | Path):
        self.db = sqlite3.connect(path)
        self.db.execute('PRAGMA foreign_keys = ON')

    def initialize(self, readings: dict[str, object] | None = None,
                   seed: tuple[int, object] | None = None) -> None:
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS sessions (
                session_id INTEGER PRIMARY KEY AUTOINCREMENT,
                mode TEXT NOT NULL DEFAULT 'open' CHECK (mode IN ('open', 'seal', 'close'))
            );
            CREATE TABLE IF NOT EXISTS readings (
                bottle_id TEXT PRIMARY KEY NOT NULL,
                raw_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS commands (
                queue_id INTEGER PRIMARY KEY,
                session_id INTEGER NOT NULL REFERENCES sessions(session_id),
                command_id TEXT NOT NULL,
                bottle_id TEXT NOT NULL REFERENCES readings(bottle_id),
                state TEXT NOT NULL CHECK (state IN ('queued', 'running', 'completed', 'cancelled')),
                completion_number INTEGER UNIQUE,
                UNIQUE(session_id, command_id),
                CHECK ((state = 'completed') = (completion_number IS NOT NULL))
            );
            CREATE UNIQUE INDEX IF NOT EXISTS one_running ON commands(state)
            WHERE state = 'running';
            CREATE TABLE IF NOT EXISTS latch (
                slot INTEGER PRIMARY KEY CHECK (slot = 1),
                completion_number INTEGER NOT NULL CHECK (completion_number >= 0),
                raw_json TEXT
            );
            CREATE TABLE IF NOT EXISTS actions (
                action_id INTEGER PRIMARY KEY,
                session_id INTEGER NOT NULL,
                command_id TEXT NOT NULL,
                bottle_id TEXT NOT NULL,
                completion_number INTEGER UNIQUE,
                UNIQUE(session_id, command_id),
                FOREIGN KEY (session_id, command_id) REFERENCES commands(session_id, command_id)
            );
        ''')
        if seed is None:
            start, raw_json = 0, None
        else:
            start, raw_code = seed
            if type(start) is not int or start < 0:
                raise ValueError('initial completion number must be a nonnegative integer')
            raw_json = json.dumps(raw_code, sort_keys=True, allow_nan=False)
        with self.transaction() as db:
            db.execute('INSERT INTO latch VALUES (1, ?, ?) ON CONFLICT(slot) DO NOTHING',
                       (start, raw_json))
            for bottle_id, raw_code in (readings or {}).items():
                if not isinstance(bottle_id, str) or not bottle_id.strip():
                    raise ValueError('recorded bottle ID must be nonblank text')
                encoded = json.dumps(raw_code, sort_keys=True, allow_nan=False)
                db.execute('INSERT INTO readings VALUES (?, ?) ON CONFLICT(bottle_id) DO NOTHING',
                           (bottle_id, encoded))
                (original,) = db.execute('SELECT raw_json FROM readings WHERE bottle_id = ?',
                                         (bottle_id,)).fetchone()
                if original != encoded:
                    raise ValueError(f'recorded bottle {bottle_id!r} already has a reading')

    @contextmanager
    def transaction(self):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            yield self.db
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def read_actions(self) -> tuple[RecordedAction, ...]:
        rows = self.db.execute('''
            SELECT session_id, command_id, bottle_id, completion_number FROM actions
            ORDER BY action_id
        ''')
        return tuple(RecordedAction(*row) for row in rows)

    def close(self) -> None:
        self.db.close()


def read_recorded_code(db: sqlite3.Connection, bottle_id: str) -> object:
    row = db.execute('SELECT raw_json FROM readings WHERE bottle_id = ?', (bottle_id,)).fetchone()
    if row is None:
        raise DeviceError(f'no recorded reading for bottle {bottle_id!r}')
    return json.loads(row[0])


def record_action(db: sqlite3.Connection, session_id: int, command_id: str, bottle_id: str,
                  completion_number: int | None = None) -> None:
    if completion_number is None:
        db.execute('INSERT INTO actions(session_id, command_id, bottle_id) VALUES (?, ?, ?)',
                   (session_id, command_id, bottle_id))
        return
    changed = db.execute('''
        UPDATE actions SET completion_number = ?
        WHERE session_id = ? AND command_id = ? AND bottle_id = ? AND completion_number IS NULL
    ''', (completion_number, session_id, command_id, bottle_id)).rowcount
    if changed != 1:
        raise DeviceError('the running action is missing from the device ledger')
