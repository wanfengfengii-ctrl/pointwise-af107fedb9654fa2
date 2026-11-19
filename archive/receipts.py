import json
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Receipt:
    receipt_id: str
    order_id: str
    completion_number: int

    def encode(self) -> str:
        return json.dumps(asdict(self), separators=(',', ':'))


def make_receipt(order_id: str, completion_number: int) -> Receipt:
    return Receipt(f'{completion_number}/{order_id}', order_id, completion_number)


def decode_receipt(encoded: str) -> Receipt:
    return Receipt(**json.loads(encoded))
