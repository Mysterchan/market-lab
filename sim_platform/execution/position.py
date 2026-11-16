"""Stable execution operation locations shared by audit and replay."""

from __future__ import annotations

from typing import Any, Dict

from sim_platform.domain.operation_position import OperationPosition

from .models import Frontier


def validate_agent_position(value: Dict[str, Any]) -> None:
    """Validate the detached operation location before financial processing."""
    OperationPosition.from_wire(value)


def position(last: Frontier) -> Dict[str, int]:
    """Stable executed-operation location for audit and replay."""
    return dict(
        sequence=last['market_sequence'],
        time_ns=last['market']['time_ns'],
        boundary=last.get('boundary', 0),
        ordinal=last.get('ordinal', 0),
    )
