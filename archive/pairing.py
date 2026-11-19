import sqlite3
from dataclasses import dataclass
from fractions import Fraction

from archive.evidence import VerifiedRawSample
from archive.publication import CommitRejected
from measurement.conversion import MeasuredValue
from orders.batches import load_batch
from orders.pairs import PairRegistration, load_pair


@dataclass(frozen=True)
class PairMember:
    order_id: str
    bottle_id: str
    completion_number: int
    raw_code: int
    measured: MeasuredValue


@dataclass(frozen=True)
class PairResult:
    pair_id: str
    batch_id: str
    profile_id: str
    threshold: Fraction
    difference: Fraction
    decision: str
    receipt_id: str
    members: tuple


@dataclass(frozen=True)
class StagedPairSample:
    pair_id: str
    order_id: str
    completion_number: int
    raw_code: int
    measured: MeasuredValue


def initialize_pairing(db: sqlite3.Connection) -> None:
    db.executescript('''
        CREATE TABLE IF NOT EXISTS pair_evidence (
            order_id TEXT PRIMARY KEY NOT NULL REFERENCES jobs(order_id),
            pair_id TEXT NOT NULL REFERENCES pairs(pair_id),
            completion_number INTEGER NOT NULL,
            raw_code TEXT NOT NULL,
            numerator TEXT NOT NULL,
            denominator TEXT NOT NULL,
            unit TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS pair_results (
            pair_id TEXT PRIMARY KEY NOT NULL REFERENCES pairs(pair_id),
            receipt_id TEXT NOT NULL,
            difference_num TEXT NOT NULL,
            difference_den TEXT NOT NULL,
            decision TEXT NOT NULL CHECK (decision IN ('pass', 'over_limit'))
        );
    ''')


def _staged_from_row(row: tuple) -> StagedPairSample:
    pair_id, order_id, number, raw_code, numerator, denominator, unit = row
    return StagedPairSample(pair_id, order_id, number, int(raw_code),
                            MeasuredValue(Fraction(int(numerator), int(denominator)), unit))


def load_pair_evidence(db: sqlite3.Connection, order_id: str) -> StagedPairSample | None:
    row = db.execute('''
        SELECT pair_id, order_id, completion_number, raw_code, numerator, denominator, unit
        FROM pair_evidence WHERE order_id = ?
    ''', (order_id,)).fetchone()
    return None if row is None else _staged_from_row(row)


def preserve_pair_sample(db: sqlite3.Connection, pair: PairRegistration,
                         evidence: VerifiedRawSample, measured: MeasuredValue) -> StagedPairSample:
    """Durably preserve the first member's actual raw sample in one commit."""
    order_id = evidence.job.order_id
    if order_id != pair.first_order_id:
        raise CommitRejected(f'order {order_id!r} is not the first member of pair {pair.pair_id!r}')
    db.execute('BEGIN IMMEDIATE')
    try:
        staged = load_pair_evidence(db, order_id)
        if staged is None:
            db.execute('INSERT INTO pair_evidence VALUES (?, ?, ?, ?, ?, ?, ?)',
                       (order_id, pair.pair_id, evidence.completion_number, str(evidence.raw_code),
                        str(measured.value.numerator), str(measured.value.denominator),
                        measured.unit))
            staged = load_pair_evidence(db, order_id)
            if staged is None:
                raise sqlite3.IntegrityError('the first pair sample could not be preserved')
        db.commit()
    except sqlite3.Error as error:
        db.rollback()
        raise CommitRejected(str(error)) from error
    except BaseException:
        db.rollback()
        raise
    return staged


def _load_member(db: sqlite3.Connection, order_id: str) -> PairMember | None:
    row = db.execute('''
        SELECT e.completion_number, e.raw_code, e.numerator, e.denominator, e.unit, j.bottle_id
        FROM pair_evidence AS e JOIN jobs AS j ON j.order_id = e.order_id
        WHERE e.order_id = ?
    ''', (order_id,)).fetchone()
    if row is None:
        return None
    number, raw_code, numerator, denominator, unit, bottle_id = row
    return PairMember(order_id, bottle_id, number, int(raw_code),
                      MeasuredValue(Fraction(int(numerator), int(denominator)), unit))


def _build_result(db: sqlite3.Connection, pair: PairRegistration) -> PairResult | None:
    row = db.execute('''
        SELECT receipt_id, difference_num, difference_den, decision
        FROM pair_results WHERE pair_id = ?
    ''', (pair.pair_id,)).fetchone()
    if row is None:
        return None
    receipt_id, difference_num, difference_den, decision = row
    members = tuple(_load_member(db, order_id)
                    for order_id in (pair.first_order_id, pair.second_order_id))
    if any(member is None for member in members):
        raise sqlite3.IntegrityError('a signed pair result is missing member evidence')
    binding = load_batch(db, pair.batch_id)
    return PairResult(pair.pair_id, pair.batch_id, binding.profile_id, pair.threshold,
                      Fraction(int(difference_num), int(difference_den)), decision,
                      receipt_id, members)


def load_pair_result(db: sqlite3.Connection, pair_id: str) -> PairResult | None:
    pair = load_pair(db, pair_id)
    return None if pair is None else _build_result(db, pair)


def sign_pair_result(db: sqlite3.Connection, pair: PairRegistration,
                     evidence: VerifiedRawSample, measured: MeasuredValue) -> PairResult:
    """Fix the pair receipt, decision, and both raw samples in one durable commit."""
    order_id = evidence.job.order_id
    if order_id != pair.second_order_id:
        raise CommitRejected(f'order {order_id!r} is not the last member of pair {pair.pair_id!r}')
    db.execute('BEGIN IMMEDIATE')
    try:
        if _build_result(db, pair) is None:
            first = load_pair_evidence(db, pair.first_order_id)
            if first is None:
                raise sqlite3.IntegrityError('the first pair member has no preserved sample')
            if load_pair_evidence(db, order_id) is None:
                db.execute('INSERT INTO pair_evidence VALUES (?, ?, ?, ?, ?, ?, ?)',
                           (order_id, pair.pair_id, evidence.completion_number,
                            str(evidence.raw_code), str(measured.value.numerator),
                            str(measured.value.denominator), measured.unit))
            difference = abs(measured.value - first.measured.value)
            decision = 'pass' if difference <= pair.threshold else 'over_limit'
            receipt_id = f'{first.completion_number}+{evidence.completion_number}/{pair.pair_id}'
            db.execute('INSERT INTO pair_results VALUES (?, ?, ?, ?, ?)',
                       (pair.pair_id, receipt_id, str(difference.numerator),
                        str(difference.denominator), decision))
        db.commit()
    except sqlite3.Error as error:
        db.rollback()
        raise CommitRejected(str(error)) from error
    except BaseException:
        db.rollback()
        raise
    result = _build_result(db, pair)
    if result is None:
        raise CommitRejected('the pair result could not be signed')
    return result
