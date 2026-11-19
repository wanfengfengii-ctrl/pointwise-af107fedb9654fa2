import sqlite3
from dataclasses import dataclass
from fractions import Fraction

from archive.evidence import VerifiedRawSample
from archive.receipts import Receipt, decode_receipt, make_receipt
from archive.store import ArchiveStore
from measurement.conversion import MeasuredValue


class CommitRejected(RuntimeError):
    """The archive did not commit a new sample and receipt."""


@dataclass(frozen=True)
class PublishedSample:
    order_id: str
    batch_id: str
    bottle_id: str
    completion_number: int
    raw_code: int
    measured: MeasuredValue
    receipt: Receipt

    @classmethod
    def from_row(cls, row: tuple) -> 'PublishedSample':
        order_id, batch_id, bottle_id, number, raw_code, numerator, denominator, unit, payload = row
        receipt = decode_receipt(payload)
        if (receipt.order_id, receipt.completion_number) != (order_id, number):
            raise sqlite3.IntegrityError('stored receipt belongs to a different sample')
        measured = MeasuredValue(Fraction(int(numerator), int(denominator)), unit)
        return cls(order_id, batch_id, bottle_id, number, int(raw_code), measured, receipt)


def publish_sample(store: ArchiveStore, evidence: VerifiedRawSample,
                   measured: MeasuredValue) -> PublishedSample:
    order_id = evidence.job.order_id
    receipt = make_receipt(order_id, evidence.completion_number)
    try:
        row = store.commit_pair(order_id, evidence.completion_number, evidence.raw_code,
                                measured.value.numerator, measured.value.denominator,
                                measured.unit, receipt.encode())
    except sqlite3.Error as error:
        raise CommitRejected(str(error)) from error
    return PublishedSample.from_row(row)
