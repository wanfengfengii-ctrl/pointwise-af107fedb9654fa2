import sqlite3
from dataclasses import dataclass
from fractions import Fraction

from orders.batches import load_batch
from orders.bottles import BottleJob, validate_identifier, validate_order_item
from orders.queue import append_jobs, find_job


@dataclass(frozen=True)
class PairRegistration:
    pair_id: str
    batch_id: str
    threshold: Fraction
    first_order_id: str
    second_order_id: str


def initialize_pairs(db: sqlite3.Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS pairs (
            pair_id TEXT PRIMARY KEY NOT NULL,
            batch_id TEXT NOT NULL REFERENCES batches(batch_id),
            threshold_num TEXT NOT NULL,
            threshold_den TEXT NOT NULL,
            first_order_id TEXT NOT NULL UNIQUE REFERENCES jobs(order_id),
            second_order_id TEXT NOT NULL UNIQUE REFERENCES jobs(order_id)
        )
    ''')


def parse_threshold(value: object) -> Fraction:
    if isinstance(value, bool):
        raise ValueError('pair threshold must be an exact rational, not a boolean')
    if isinstance(value, int):
        threshold = Fraction(value)
    elif isinstance(value, Fraction):
        threshold = value
    elif isinstance(value, str):
        try:
            threshold = Fraction(value)
        except (ValueError, ZeroDivisionError) as error:
            raise ValueError(f'invalid pair threshold {value!r}') from error
    else:
        raise ValueError('pair threshold must be a nonnegative integer, rational string, or Fraction')
    if threshold < 0:
        raise ValueError('pair threshold must be nonnegative')
    return threshold


def _registration_from_row(row: tuple) -> PairRegistration:
    pair_id, batch_id, numerator, denominator, first_order_id, second_order_id = row
    return PairRegistration(pair_id, batch_id, Fraction(int(numerator), int(denominator)),
                            first_order_id, second_order_id)


_PAIR_COLUMNS = 'pair_id, batch_id, threshold_num, threshold_den, first_order_id, second_order_id'


def load_pair(db: sqlite3.Connection, pair_id: str) -> PairRegistration | None:
    row = db.execute(f'SELECT {_PAIR_COLUMNS} FROM pairs WHERE pair_id = ?', (pair_id,)).fetchone()
    return None if row is None else _registration_from_row(row)


def find_pair_for_order(db: sqlite3.Connection, order_id: str) -> PairRegistration | None:
    row = db.execute(f'''
        SELECT {_PAIR_COLUMNS} FROM pairs WHERE first_order_id = ? OR second_order_id = ?
    ''', (order_id, order_id)).fetchone()
    return None if row is None else _registration_from_row(row)


def validate_pair_members(members: object) -> tuple[tuple[str, str], tuple[str, str]]:
    if not isinstance(members, (list, tuple)) or len(members) != 2:
        raise ValueError('pair members must be an ordered pair of orders')
    first = validate_order_item(members[0])
    second = validate_order_item(members[1])
    if first[0] == second[0]:
        raise ValueError('pair order IDs must differ')
    if first[1] == second[1]:
        raise ValueError('pair bottle IDs must differ')
    return first, second


def submit_pair(db: sqlite3.Connection, batch_id: str, pair_id: str, members: list | tuple,
                threshold: object) -> tuple[BottleJob, BottleJob]:
    pair_id = validate_identifier(pair_id, 'pair ID')
    first, second = validate_pair_members(members)
    threshold = parse_threshold(threshold)
    with db:
        db.execute('BEGIN IMMEDIATE')
        load_batch(db, batch_id)
        existing = load_pair(db, pair_id)
        if existing is not None:
            jobs = (find_job(db, existing.first_order_id), find_job(db, existing.second_order_id))
            original_members = ((jobs[0].order_id, jobs[0].bottle_id),
                                (jobs[1].order_id, jobs[1].bottle_id))
            if (existing.batch_id, existing.threshold, original_members) != (
                batch_id, threshold, (first, second)
            ):
                raise ValueError(f'pair {pair_id!r} is already registered with different parameters')
            return jobs
        for order_id in (first[0], second[0]):
            if find_job(db, order_id) is not None:
                raise ValueError(f'order {order_id!r} is already assigned')
        added = append_jobs(db, batch_id, (first, second))
        db.execute('INSERT INTO pairs VALUES (?, ?, ?, ?, ?, ?)',
                   (pair_id, batch_id, str(threshold.numerator), str(threshold.denominator),
                    first[0], second[0]))
        return added[0], added[1]
