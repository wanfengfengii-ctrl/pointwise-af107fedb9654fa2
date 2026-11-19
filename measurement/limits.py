from dataclasses import dataclass


class InvalidReading(ValueError):
    """The device returned a value outside the fixed integer input contract."""


@dataclass(frozen=True)
class RawCodeRange:
    minimum: int
    maximum: int

    def __post_init__(self) -> None:
        if type(self.minimum) is not int or type(self.maximum) is not int:
            raise ValueError('raw limits must be integers')
        if self.minimum > self.maximum:
            raise ValueError('raw limits must be ordered')

    def check(self, raw_code: object) -> int:
        if type(raw_code) is not int:
            raise InvalidReading('raw code must be an integer')
        if not self.minimum <= raw_code <= self.maximum:
            raise InvalidReading(f'raw code {raw_code} is outside [{self.minimum}, {self.maximum}]')
        return raw_code
