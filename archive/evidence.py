from dataclasses import dataclass

from control.completion import CompletedAspiration
from device.latch import DeviceSample
from orders.bottles import BottleJob


class EvidenceMismatch(ValueError):
    """The retained device reading cannot establish the requested physical sample."""


@dataclass(frozen=True)
class VerifiedRawSample:
    job: BottleJob
    completion_number: int
    raw_code: object


def verify_latest_sample(completion: CompletedAspiration,
                         latest: DeviceSample | None) -> VerifiedRawSample:
    if latest is None:
        raise EvidenceMismatch('the device has no retained sample')
    if latest.completion_number != completion.completion_number:
        raise EvidenceMismatch(
            f'expected device completion {completion.completion_number}, '
            f'but the retained sample is {latest.completion_number}'
        )
    return VerifiedRawSample(completion.job, latest.completion_number, latest.raw_code)
