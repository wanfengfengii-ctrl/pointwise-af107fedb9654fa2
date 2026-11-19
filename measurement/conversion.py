from dataclasses import dataclass
from fractions import Fraction

from measurement.profiles import CalibrationProfile


@dataclass(frozen=True)
class MeasuredValue:
    value: Fraction
    unit: str


def convert_reading(profile: CalibrationProfile, raw_code: object) -> MeasuredValue:
    code = profile.limits.check(raw_code)
    value = profile.gain * code + profile.offset
    return MeasuredValue(value, profile.unit)
