from dataclasses import dataclass
from fractions import Fraction

from measurement.limits import RawCodeRange


@dataclass(frozen=True)
class CalibrationProfile:
    profile_id: str
    limits: RawCodeRange
    gain: Fraction
    offset: Fraction
    unit: str


def _coefficient(value: object) -> Fraction:
    if type(value) not in (str, int, Fraction):
        raise ValueError('a coefficient must be an exact rational value')
    try:
        return Fraction(value)
    except (ValueError, ZeroDivisionError) as error:
        raise ValueError(f'invalid rational coefficient {value!r}') from error


def build_profile(profile_id: str, minimum: int, maximum: int, gain: int | str | Fraction,
                  offset: int | str | Fraction, unit: str) -> CalibrationProfile:
    if not isinstance(profile_id, str) or not profile_id.strip():
        raise ValueError('calibration ID must be nonblank text')
    if not isinstance(unit, str) or not unit.strip():
        raise ValueError('unit must be nonblank text')
    return CalibrationProfile(profile_id, RawCodeRange(minimum, maximum),
                              _coefficient(gain), _coefficient(offset), unit)
