import sqlite3
from dataclasses import dataclass

from control.active import ActiveCollection, record_completion
from device.aspiration import AspirationOutcome
from orders.bottles import BottleJob


@dataclass(frozen=True)
class CompletedAspiration:
    attempt: ActiveCollection

    def __post_init__(self) -> None:
        if self.attempt.completion_number is None:
            raise ValueError('the active attempt has no confirmed completion')

    @property
    def job(self) -> BottleJob:
        return self.attempt.job

    @property
    def completion_number(self) -> int:
        number = self.attempt.completion_number
        assert number is not None
        return number


def bind_completion(db: sqlite3.Connection, attempt: ActiveCollection,
                    outcome: AspirationOutcome) -> CompletedAspiration | None:
    if outcome.state != 'completed' or outcome.completion_number is None:
        return None
    if (outcome.session_id, outcome.command_id, outcome.bottle_id) != (
        attempt.session_id, attempt.command_id, attempt.job.bottle_id
    ):
        return None
    recorded = record_completion(db, attempt, outcome.completion_number)
    return None if recorded is None else CompletedAspiration(recorded)
