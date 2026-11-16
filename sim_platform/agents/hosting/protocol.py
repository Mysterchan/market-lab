"""Bounded JSON transport for trusted local agent processes.

Only detached values cross this boundary. One reader admits requests, one writer
sends results; pipeline callbacks only enqueue. Slots remain reserved until a
reply is delivered, so an agent that stops reading cannot grow host memory.
This transport is not an OS sandbox or a durable receipt store.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Dict, NoReturn

from sim_platform.domain.json_values import encode
from sim_platform.domain.models import ContractError, integer


# Default request admission rate per Agent.
DEFAULT_REQUESTS_PER_SECOND = 200


@dataclass(frozen=True)
class TransportLimits:
    """Bounds for one Agent pipe: request/response bytes, pending requests and rate."""

    request_bytes: int
    response_bytes: int
    pending: int
    requests_per_second: int = DEFAULT_REQUESTS_PER_SECOND

    def __post_init__(self) -> None:
        for name in ('request_bytes', 'response_bytes', 'pending', 'requests_per_second'):
            integer(getattr(self, name), name, 1)

    @classmethod
    def from_configuration(cls, limits: Dict[str, Any]) -> TransportLimits:
        return cls(
            limits['tool_request_bytes'], limits['tool_response_bytes'], limits['pending_requests']
        )


def packet(value: Any, maximum: int) -> bytes:
    raw = encode(value).encode('utf-8')
    if len(raw) > maximum:
        raise ContractError('IPC message exceeds byte limit')
    return raw


def reject_constant(value: str) -> NoReturn:
    raise ValueError('non-finite JSON constant')


def unpack(raw: bytes) -> Dict[str, Any]:
    try:
        value = json.loads(raw, parse_constant=reject_constant)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ContractError('invalid IPC JSON') from exc
    if type(value) is not dict or set(value) not in (
        {'request'},
        {'request_id', 'command'},
        {'request_id', 'result', 'error'},
    ):
        raise ContractError('unsupported IPC envelope')
    return value
