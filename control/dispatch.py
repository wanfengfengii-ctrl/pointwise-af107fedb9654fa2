import sqlite3

from control.active import ActiveCollection, load_active, reserve_attempt
from control.completion import bind_completion
from control.progression import StepResult, current_head, deliver_head
from device.port import DevicePort
from device.recording import DeviceError
from orders.batches import load_batch
from orders.bottles import BottleJob


def issue_attempt(db: sqlite3.Connection, port: DevicePort | None, job: BottleJob,
                  prior: ActiveCollection | None = None) -> StepResult:
    if port is None:
        return StepResult('source_read_failed', job.order_id, detail='device is offline')
    try:
        session_id = port.open_session()
    except (DeviceError, sqlite3.Error) as error:
        return StepResult('source_read_failed', job.order_id, detail=str(error))
    command_id = f'sample-{session_id}'
    attempt = reserve_attempt(db, job, session_id, command_id, prior)
    if attempt is None:
        port.seal(session_id)
        return StepResult('pending', job.order_id, detail='dispatch eligibility changed')
    try:
        outcome = port.submit(session_id, command_id, job.bottle_id)
        if outcome is None:
            return StepResult('pending', job.order_id)
        port.seal(session_id)
    except (DeviceError, sqlite3.Error) as error:
        return StepResult('source_read_failed', job.order_id, detail=str(error))
    completion = bind_completion(db, attempt, outcome)
    if completion is None:
        return StepResult('pending', job.order_id, detail='completion could not be bound')
    return deliver_head(db, port, completion)


def advance(db: sqlite3.Connection, port: DevicePort | None, batch_id: str) -> StepResult:
    load_batch(db, batch_id)
    if load_active(db) is not None:
        from control.recovery import resume

        return resume(db, port, batch_id)
    job = current_head(db, batch_id)
    if job is None:
        return StepResult('idle')
    return issue_attempt(db, port, job)
