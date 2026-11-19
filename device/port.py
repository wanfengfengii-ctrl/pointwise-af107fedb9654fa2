from device import aspiration, latch, sessions
from device.aspiration import AspirationOutcome
from device.latch import DeviceSample
from device.recording import DeviceError, DeviceRecording
from device.sessions import SessionTerminal


class DevicePort:
    """Explicitly stepped single-device recording, with independently delivered notices."""

    def __init__(self, recording: DeviceRecording, automatic: bool = True):
        self.recording = recording
        self.automatic = automatic
        self.reads_available = True
        self._notices: list[DeviceSample] = []

    def open_session(self) -> int:
        with self.recording.transaction() as db:
            return sessions.open_session(db)

    def submit(self, session_id: int, command_id: str, bottle_id: str) -> AspirationOutcome | None:
        with self.recording.transaction() as db:
            current = aspiration.enqueue_command(db, session_id, command_id, bottle_id)
        if current.state == 'completed':
            return current
        if not self.automatic or current.state == 'cancelled':
            return None
        for _ in range(2):
            self.tick()
            current = aspiration.command_outcome(self.recording.db, session_id, command_id)
            if current is not None and current.state == 'completed':
                return current
        return None

    def tick(self) -> AspirationOutcome | None:
        with self.recording.transaction() as db:
            finished = aspiration.finish_command(db)
            if finished is None:
                return aspiration.start_command(db)
        outcome, sample = finished
        self.queue_notice(sample)
        return outcome

    def seal(self, session_id: int) -> None:
        with self.recording.transaction() as db:
            sessions.seal_session(db, session_id)

    def close(self, session_id: int) -> None:
        with self.recording.transaction() as db:
            sessions.close_session(db, session_id)

    def terminal(self, session_id: int) -> SessionTerminal | None:
        with self.recording.transaction() as db:
            return sessions.read_terminal(db, session_id)

    def read_latest(self) -> DeviceSample | None:
        if not self.reads_available:
            raise DeviceError('latest device sample is temporarily unavailable')
        return latch.read_latest(self.recording.db)

    def queue_notice(self, sample: DeviceSample) -> None:
        self._notices.append(sample)

    def drain_notices(self) -> tuple[DeviceSample, ...]:
        notices = tuple(self._notices)
        self._notices.clear()
        return notices
