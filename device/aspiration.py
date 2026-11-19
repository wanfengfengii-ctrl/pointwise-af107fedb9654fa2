import sqlite3
from dataclasses import dataclass

from device.latch import DeviceSample, write_completion
from device.recording import DeviceError, read_recorded_code, record_action


@dataclass(frozen=True)
class AspirationOutcome:
    session_id: int
    command_id: str
    bottle_id: str
    state: str
    completion_number: int | None


_OUTCOME_COLUMNS = 'session_id, command_id, bottle_id, state, completion_number'


def command_outcome(db: sqlite3.Connection, session_id: int,
                    command_id: str) -> AspirationOutcome | None:
    row = db.execute(f'''
        SELECT {_OUTCOME_COLUMNS} FROM commands WHERE session_id = ? AND command_id = ?
    ''', (session_id, command_id)).fetchone()
    return None if row is None else AspirationOutcome(*row)


def enqueue_command(db: sqlite3.Connection, session_id: int, command_id: str,
                    bottle_id: str) -> AspirationOutcome:
    from device.sessions import accepts_commands

    if not isinstance(command_id, str) or not command_id.strip():
        raise ValueError('command ID must be nonblank text')
    existing = command_outcome(db, session_id, command_id)
    if existing is not None:
        if existing.bottle_id != bottle_id:
            raise DeviceError('command ID was used for a different bottle')
        return existing
    if not accepts_commands(db, session_id):
        raise DeviceError('session is no longer accepting commands')
    read_recorded_code(db, bottle_id)
    db.execute('''
        INSERT INTO commands(session_id, command_id, bottle_id, state) VALUES (?, ?, ?, 'queued')
    ''', (session_id, command_id, bottle_id))
    return AspirationOutcome(session_id, command_id, bottle_id, 'queued', None)


def start_command(db: sqlite3.Connection) -> AspirationOutcome | None:
    if db.execute("SELECT 1 FROM commands WHERE state = 'running'").fetchone() is not None:
        return None
    row = db.execute('''
        SELECT c.session_id, c.command_id, c.bottle_id FROM commands AS c
        JOIN sessions AS s USING (session_id)
        WHERE c.state = 'queued' AND s.mode IN ('open', 'seal')
        ORDER BY c.queue_id LIMIT 1
    ''').fetchone()
    if row is None:
        return None
    session_id, command_id, bottle_id = row
    db.execute("UPDATE commands SET state = 'running' WHERE session_id = ? AND command_id = ?",
               (session_id, command_id))
    record_action(db, session_id, command_id, bottle_id)
    return AspirationOutcome(session_id, command_id, bottle_id, 'running', None)


def finish_command(db: sqlite3.Connection) -> tuple[AspirationOutcome, DeviceSample] | None:
    row = db.execute('''
        SELECT session_id, command_id, bottle_id FROM commands WHERE state = 'running'
    ''').fetchone()
    if row is None:
        return None
    session_id, command_id, bottle_id = row
    sample = write_completion(db, read_recorded_code(db, bottle_id))
    db.execute('''
        UPDATE commands SET state = 'completed', completion_number = ?
        WHERE session_id = ? AND command_id = ?
    ''', (sample.completion_number, session_id, command_id))
    record_action(db, session_id, command_id, bottle_id, sample.completion_number)
    return AspirationOutcome(session_id, command_id, bottle_id, 'completed',
                             sample.completion_number), sample


def cancel_queued_commands(db: sqlite3.Connection, session_id: int) -> None:
    db.execute("UPDATE commands SET state = 'cancelled' WHERE session_id = ? AND state = 'queued'",
               (session_id,))


def session_outcomes(db: sqlite3.Connection, session_id: int) -> tuple[AspirationOutcome, ...]:
    rows = db.execute(f'''
        SELECT {_OUTCOME_COLUMNS} FROM commands WHERE session_id = ? ORDER BY queue_id
    ''', (session_id,))
    return tuple(AspirationOutcome(*row) for row in rows)
