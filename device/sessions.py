import sqlite3
from dataclasses import dataclass

from device.aspiration import AspirationOutcome, cancel_queued_commands, session_outcomes
from device.recording import DeviceError


@dataclass(frozen=True)
class SessionTerminal:
    session_id: int
    mode: str
    outcomes: tuple[AspirationOutcome, ...]

    def outcome_for(self, command_id: str) -> AspirationOutcome | None:
        return next((outcome for outcome in self.outcomes if outcome.command_id == command_id), None)


def open_session(db: sqlite3.Connection) -> int:
    return db.execute('INSERT INTO sessions DEFAULT VALUES').lastrowid


def _mode(db: sqlite3.Connection, session_id: int) -> str:
    row = db.execute('SELECT mode FROM sessions WHERE session_id = ?', (session_id,)).fetchone()
    if row is None:
        raise DeviceError(f'unknown device session {session_id}')
    return row[0]


def accepts_commands(db: sqlite3.Connection, session_id: int) -> bool:
    return _mode(db, session_id) == 'open'


def seal_session(db: sqlite3.Connection, session_id: int) -> None:
    if accepts_commands(db, session_id):
        db.execute("UPDATE sessions SET mode = 'seal' WHERE session_id = ?", (session_id,))


def close_session(db: sqlite3.Connection, session_id: int) -> None:
    if accepts_commands(db, session_id):
        db.execute("UPDATE sessions SET mode = 'close' WHERE session_id = ?", (session_id,))
        cancel_queued_commands(db, session_id)


def read_terminal(db: sqlite3.Connection, session_id: int) -> SessionTerminal | None:
    mode = _mode(db, session_id)
    if mode == 'open':
        return None
    outcomes = session_outcomes(db, session_id)
    if any(outcome.state in ('queued', 'running') for outcome in outcomes):
        return None
    return SessionTerminal(session_id, mode, outcomes)
