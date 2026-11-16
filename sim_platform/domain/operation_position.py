"""A financial operation location, independent of transport or execution owners."""

from dataclasses import dataclass

from .models import ContractError


@dataclass(frozen=True)
class OperationPosition:
    """Integer nanoseconds and counters; JSON conversion happens at the boundary.

    sequence is the executed financial source prefix, boundary the merged event
    prefix, and ordinal the operation/publication order. They are not aliases.
    """

    sequence: int
    time_ns: int
    boundary: int
    ordinal: int

    def __post_init__(self) -> None:
        for value in (self.sequence, self.time_ns, self.boundary, self.ordinal):
            if type(value) is not int or not 0 <= value < 10**20:
                raise ContractError('invalid operation position counter')

    def to_wire(self) -> dict:
        return dict(
            sequence=str(self.sequence),
            time_ns=str(self.time_ns),
            boundary=str(self.boundary),
            ordinal=str(self.ordinal),
        )

    @classmethod
    def from_wire(cls, value: dict) -> 'OperationPosition':
        names = ('sequence', 'time_ns', 'boundary', 'ordinal')
        if type(value) is not dict or set(value) != set(names):
            raise ContractError('invalid operation position fields')
        for counter in value.values():
            if (
                not isinstance(counter, str)
                or not counter.isascii()
                or not counter.isdecimal()
                or len(counter) > 20
                or len(counter) > 1
                and counter.startswith('0')
            ):
                raise ContractError('invalid operation position counter')
        return cls(*(int(value[name]) for name in names))
