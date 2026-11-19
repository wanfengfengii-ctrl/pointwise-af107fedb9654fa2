import sqlite3
from dataclasses import dataclass

from archive.evidence import EvidenceMismatch, verify_latest_sample
from archive.pairing import (PairResult, load_pair_evidence, load_pair_result,
                             preserve_pair_sample, sign_pair_result)
from archive.publication import CommitRejected, PublishedSample, publish_sample
from archive.retrieval import retrieve
from archive.store import ArchiveStore
from control.active import ActiveCollection, confirm_delivery, load_progress
from control.completion import CompletedAspiration
from device.port import DevicePort
from device.recording import DeviceError
from measurement.calibration import load_calibration
from measurement.conversion import convert_reading
from measurement.limits import InvalidReading
from orders.batches import load_batch
from orders.bottles import BottleJob
from orders.pairs import PairRegistration, find_pair_for_order
from orders.queue import jobs_after


@dataclass(frozen=True)
class StepResult:
    status: str
    order_id: str | None = None
    sample: PublishedSample | None = None
    detail: str | None = None
    pair: PairResult | None = None


def current_head(db: sqlite3.Connection, batch_id: str) -> BottleJob | None:
    following = jobs_after(db, batch_id, load_progress(db, batch_id), limit=1)
    return following[0] if following else None


def _confirm_published(db: sqlite3.Connection, attempt: ActiveCollection,
                       sample: PublishedSample) -> StepResult:
    job = attempt.job
    if (sample.order_id, sample.batch_id, sample.bottle_id, sample.completion_number) != (
        job.order_id, job.batch_id, job.bottle_id, attempt.completion_number
    ):
        return StepResult('source_read_failed', job.order_id,
                          detail='committed sample does not match the active completion')
    try:
        confirmed = confirm_delivery(db, attempt, sample.completion_number)
    except sqlite3.Error as error:
        return StepResult('checkpoint_failed', job.order_id, detail=str(error))
    if not confirmed:
        return StepResult('pending', job.order_id, detail='active attempt changed')
    return StepResult('published', job.order_id, sample)


def reconcile_delivery(db: sqlite3.Connection, attempt: ActiveCollection) -> StepResult | None:
    stored = retrieve(ArchiveStore(db), attempt.job.order_id)
    return None if stored is None else _confirm_published(db, attempt, stored)


def last_delivery(db: sqlite3.Connection, batch_id: str) -> StepResult:
    position = load_progress(db, batch_id)
    if position == 0:
        return StepResult('idle')
    (job,) = jobs_after(db, batch_id, position - 1, limit=1)
    pair = find_pair_for_order(db, job.order_id)
    if pair is not None:
        if job.order_id == pair.first_order_id:
            if load_pair_evidence(db, job.order_id) is None:
                raise sqlite3.IntegrityError('confirmed queue position has no preserved pair sample')
            return StepResult('pair_staged', job.order_id)
        result = load_pair_result(db, pair.pair_id)
        if result is None:
            raise sqlite3.IntegrityError('confirmed queue position has no signed pair result')
        return StepResult('pair_published', job.order_id, pair=result)
    sample = retrieve(ArchiveStore(db), job.order_id)
    if sample is None:
        raise sqlite3.IntegrityError('confirmed queue position has no archived sample')
    return StepResult('published', job.order_id, sample)


def deliver_head(db: sqlite3.Connection, port: DevicePort | None,
                 completion: CompletedAspiration) -> StepResult:
    attempt = completion.attempt
    pair = find_pair_for_order(db, attempt.job.order_id)
    if pair is not None:
        return deliver_pair_head(db, port, completion, pair)
    prior_delivery = reconcile_delivery(db, attempt)
    if prior_delivery is not None:
        return prior_delivery
    order_id = completion.job.order_id
    if port is None:
        return StepResult('source_read_failed', order_id, detail='device is offline')
    try:
        evidence = verify_latest_sample(completion, port.read_latest())
    except (DeviceError, EvidenceMismatch, sqlite3.Error) as error:
        # Another worker may have archived this completion before the slot moved on.
        result = reconcile_delivery(db, attempt)
        if result is not None:
            return result
        return StepResult('source_read_failed', order_id, detail=str(error))
    binding = load_batch(db, completion.job.batch_id)
    profile = load_calibration(db, binding.profile_id)
    try:
        measured = convert_reading(profile, evidence.raw_code)
    except InvalidReading as error:
        return StepResult('invalid_reading', order_id, detail=str(error))
    try:
        published = publish_sample(ArchiveStore(db), evidence, measured)
    except CommitRejected as error:
        return StepResult('commit_failed', order_id, detail=str(error))
    return _confirm_published(db, attempt, published)


def _confirm_pair_staged(db: sqlite3.Connection, attempt: ActiveCollection,
                         staged) -> StepResult:
    job = attempt.job
    if (staged.order_id, staged.completion_number) != (job.order_id, attempt.completion_number):
        return StepResult('source_read_failed', job.order_id,
                          detail='preserved pair sample does not match the active completion')
    try:
        confirmed = confirm_delivery(db, attempt, staged.completion_number)
    except sqlite3.Error as error:
        return StepResult('checkpoint_failed', job.order_id, detail=str(error))
    if not confirmed:
        return StepResult('pending', job.order_id, detail='active attempt changed')
    return StepResult('pair_staged', job.order_id)


def _confirm_pair_published(db: sqlite3.Connection, attempt: ActiveCollection,
                            result: PairResult) -> StepResult:
    job = attempt.job
    member = result.members[1]
    if (member.order_id, member.completion_number) != (job.order_id, attempt.completion_number):
        return StepResult('source_read_failed', job.order_id,
                          detail='signed pair result does not match the active completion')
    try:
        confirmed = confirm_delivery(db, attempt, member.completion_number)
    except sqlite3.Error as error:
        return StepResult('checkpoint_failed', job.order_id, detail=str(error))
    if not confirmed:
        return StepResult('pending', job.order_id, detail='active attempt changed')
    return StepResult('pair_published', job.order_id, pair=result)


def reconcile_pair_delivery(db: sqlite3.Connection, attempt: ActiveCollection,
                            pair: PairRegistration) -> StepResult | None:
    if attempt.job.order_id == pair.first_order_id:
        staged = load_pair_evidence(db, attempt.job.order_id)
        return None if staged is None else _confirm_pair_staged(db, attempt, staged)
    result = load_pair_result(db, pair.pair_id)
    return None if result is None else _confirm_pair_published(db, attempt, result)


def deliver_pair_head(db: sqlite3.Connection, port: DevicePort | None,
                      completion: CompletedAspiration, pair: PairRegistration) -> StepResult:
    attempt = completion.attempt
    order_id = attempt.job.order_id
    reconciled = reconcile_pair_delivery(db, attempt, pair)
    if reconciled is not None:
        return reconciled
    if port is None:
        return StepResult('source_read_failed', order_id, detail='device is offline')
    try:
        evidence = verify_latest_sample(completion, port.read_latest())
    except (DeviceError, EvidenceMismatch, sqlite3.Error) as error:
        # Another worker may have committed this completion before the slot moved on.
        result = reconcile_pair_delivery(db, attempt, pair)
        if result is not None:
            return result
        return StepResult('source_read_failed', order_id, detail=str(error))
    binding = load_batch(db, attempt.job.batch_id)
    profile = load_calibration(db, binding.profile_id)
    try:
        measured = convert_reading(profile, evidence.raw_code)
    except InvalidReading as error:
        return StepResult('invalid_reading', order_id, detail=str(error))
    try:
        if order_id == pair.first_order_id:
            staged = preserve_pair_sample(db, pair, evidence, measured)
            return _confirm_pair_staged(db, attempt, staged)
        result = sign_pair_result(db, pair, evidence, measured)
    except CommitRejected as error:
        return StepResult('commit_failed', order_id, detail=str(error))
    return _confirm_pair_published(db, attempt, result)
