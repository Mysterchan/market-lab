"""A task's objective and the notification that caused it to run now."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict

from sim_platform.domain.models import ContractError


@dataclass(frozen=True)
class Task:
    """Tell the agent what to do, separately from Runtime's wake-up bookkeeping.

    instruction is the task to perform. notification carries the wake reason and
    timing/events, not current market data. Work IDs remain on AgentClient.
    """

    instruction: str
    notification: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.instruction, str) or not self.instruction.strip():
            raise ContractError('task requires a nonempty instruction')
