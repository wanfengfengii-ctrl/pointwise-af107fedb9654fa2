import sqlite3

from control.active import ActiveCollection, load_active
from control.completion import CompletedAspiration, bind_completion
from control.dispatch import issue_attempt
from control.progression import StepResult, deliver_head, last_delivery, reconcile_delivery
from device.port import DevicePort
from device.recording import DeviceError
from device.sessions import SessionTerminal
from orders.batches import load_batch


def resolve_prior_attempt(port: DevicePort, attempt: ActiveCollection) -> SessionTerminal | None:
    # Sealing is idempotent and preserves a close already chosen at the device.
    port.seal(attempt.session_id)
    return port.terminal(attempt.session_id)


def resume(db: sqlite3.Connection, port: DevicePort | None, batch_id: str) -> StepResult:
    load_batch(db, batch_id)
    attempt = load_active(db)
    if attempt is None:
        return last_delivery(db, batch_id)
    order_id = attempt.job.order_id
    if attempt.job.batch_id != batch_id:
        return StepResult('pending', detail='device is reserved by another batch')
    reconciled = reconcile_delivery(db, attempt)
    if reconciled is not None:
        return reconciled
    if attempt.completion_number is not None:
        return deliver_head(db, port, CompletedAspiration(attempt))
    if port is None:
        return StepResult('source_read_failed', order_id, detail='device is offline')
    try:
        terminal = resolve_prior_attempt(port, attempt)
    except (DeviceError, sqlite3.Error) as error:
        return StepResult('source_read_failed', order_id, detail=str(error))
    if terminal is None:
        return StepResult('pending', order_id)
    outcome = terminal.outcome_for(attempt.command_id)
    if outcome is None or outcome.state == 'cancelled':
        return issue_attempt(db, port, attempt.job, prior=attempt)
    completion = bind_completion(db, attempt, outcome)
    if completion is None:
        return StepResult('pending', order_id, detail='completion could not be bound')
    return deliver_head(db, port, completion)
