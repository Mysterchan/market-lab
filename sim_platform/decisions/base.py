"""Shared decision-run lifecycle, independent of policies, tools, SQL and UI.

Concrete runners choose how decisions are produced and how market work is
scheduled. Both stop() and close() terminate work and release owned resources;
pause() retains work. Observation/action and tool APIs stay on their subclasses.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

from sim_platform.domain.models import ContractError


class DecisionRunner(ABC):
    """Lifecycle contract for one run's decision method (Human/Rule policy or Agent).

    Platform creates a runner; the experiment's ResearchController owns it and is the
    only caller of its lifecycle methods. ``background`` is True when the runner
    advances the market on its own threads/children, so poll() only inspects progress.
    """

    background = False

    @property
    @abstractmethod
    def run_id(self) -> str:
        """Persistent run identity bound to this decision maker."""

    @property
    @abstractmethod
    def active(self) -> bool:
        """Whether ready/running/paused work can still continue."""

    @property
    @abstractmethod
    def closed(self) -> bool:
        """Whether this runner has released its execution resources."""

    @abstractmethod
    def play(self, speed: Optional[float] = None) -> Any:
        """Start or resume the runner's own scheduling mechanism."""

    @abstractmethod
    def pause(self) -> Any:
        """Suspend advancement while retaining unfinished work."""

    @abstractmethod
    def poll(self, *, max_events: int = 1, budget_seconds: Optional[float] = None) -> Any:
        """Service synchronous work or inspect background progress; never double-drive."""

    @property
    def archive_ready(self) -> bool:
        """Whether the experiment may safely read the market for its archive."""
        return True

    def detached_view(self) -> Optional[Dict[str, Any]]:
        """Return a published background view, or None for synchronous capture."""
        return None

    @property
    def policy_controls(self) -> Any:
        raise ContractError('this runner does not provide policy controls')

    @property
    def tool_controls(self) -> Any:
        raise ContractError('this runner does not provide agent tools')

    def step(self) -> Any:
        raise ContractError('this runner does not provide single-step playback')

    @abstractmethod
    def close(self) -> None:
        """Terminate and release owned execution resources; safe to repeat."""

    def stop(self) -> None:
        self.close()

    def __enter__(self) -> DecisionRunner:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
