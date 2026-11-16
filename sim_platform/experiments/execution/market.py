"""Shared financial command boundary; preserves policy-batch and tool atomicity.

Platform injects the existing coordinator capability. This object adds no SQL,
submission implementation, scheduling or execution ownership.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Dict, Optional, Protocol, Tuple

if TYPE_CHECKING:  # Annotation only.
    from sim_platform.decisions.policy.models import MarketInput
    from sim_platform.execution.session import ExecutionSession
    from sim_platform.market.models import MarketEvent, MarketSnapshot


class MarketExecution(Protocol):
    """Owner-only port; poll/control callers do not get the native market."""

    @property
    def time_ns(self) -> int: ...
    @property
    def sequence(self) -> int: ...
    @property
    def status(self) -> str: ...
    @property
    def next_event_time_ns(self) -> Optional[int]: ...
    def play(self) -> Any: ...
    def pause(self) -> Any: ...
    def step(self) -> MarketEvent: ...
    def pump(self) -> Tuple[MarketEvent, ...]: ...
    def finish(self, status: str, reason: str, time_ns: Optional[int]) -> None: ...


class ExecutionCommands:
    """Binds one run's profile and ID to the coordinator's commit_decision.

    Created by Platform or experiment assembly for one run and held by its runner.
    It owns nothing; each commit is one existing financial unit of work.

    Args:
        commit_decision: ExecutionCoordinator.commit_decision (or a test double).
        profile: Profile that owns the run.
        run_id: The run decisions are committed to.
    """

    def __init__(self, commit_decision: Callable[..., Any], profile: str, run_id: str) -> None:
        self._commit = commit_decision
        self.profile, self.run_id = profile, run_id

    def commit(
        self,
        maker: str,
        market: MarketSnapshot,
        record: Optional[Dict[str, Any]] = None,
        expected_state: Any = None,
        *,
        action: Any = None,
        position: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """Delegate one existing financial UOW; only the execution owner calls it."""
        return self._commit(
            self.profile,
            self.run_id,
            maker,
            market,
            record,
            expected_state,
            action=action,
            position=position,
        )


class LocalMarketExecution:
    """Local implementation of the market execution port, owner access only.

    Policy stepping and background market driving borrow the same Session.
    A future remote implementation replaces this port, not financial rules.
    The runner that creates it owns the ExecutionSession and market; this object
    only forwards to them and remembers the status given to finish().
    """

    def __init__(self, execution: ExecutionSession, market: MarketInput) -> None:
        self.execution, self.market = execution, market
        self._status = 'running'

    @property
    def time_ns(self) -> int:
        return self.market.time_ns

    @property
    def sequence(self) -> int:
        return self.market.snapshot.sequence

    @property
    def account_sequence(self) -> int:
        return self.execution.account_sequence

    @property
    def status(self) -> str:
        return (
            self._status
            if self.market.status not in ('completed', 'failed')
            else self.market.status
        )

    @property
    def next_event_time_ns(self) -> Optional[int]:
        return self.market.next_event_time_ns

    def play(self) -> Any:
        return self.execution.play()

    def pause(self) -> Any:
        return self.execution.pause()

    def step(self) -> MarketEvent:
        return self.execution.step()

    def pump(self) -> Tuple[MarketEvent, ...]:
        return self.execution.pump(1)

    def advance_idle_prefix(self, end_prefix: int) -> Tuple[int, Optional[MarketEvent]]:
        return self.execution.advance_idle_prefix(end_prefix)

    def finish(self, status: str, reason: str, time_ns: Optional[int]) -> None:
        if not self.execution.closed:
            self.execution.finish(status, reason, time_ns=time_ns)
        self._status = status
