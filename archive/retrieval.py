from archive.publication import PublishedSample
from archive.store import ArchiveStore
from orders.bottles import validate_identifier


def retrieve(store: ArchiveStore, order_id: str) -> PublishedSample | None:
    validate_identifier(order_id, 'order ID')
    row = store.lookup(order_id)
    return None if row is None else PublishedSample.from_row(row)
