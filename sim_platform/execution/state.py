"""Run-owned working state; publish database changes only after commit."""

from __future__ import annotations

from dataclasses import dataclass, replace
from threading import RLock
from time import monotonic
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from sim_platform.domain.models import AccountSnapshot, ContractError

from .engine import advance, snapshot_fields
from .models import ExecutionSnapshot, Fill, Frontier
from .risk import OPEN

if TYPE_CHECKING:  # Annotation only: execution does not import the market package at runtime.
    from sim_platform.market.models import MarketEvent, MarketSnapshot

# An idle run saves its position at most this often; orders and fills save immediately.
PERIODIC_CHECKPOINT_SECONDS = 30.0


class ExecutionState:
    """The financial state one running experiment keeps in memory.

    The owner of a run advances this state on every market event without touching
    the database. Anything that changes the account (an order, a fill, the end of the
    run) goes through ``operations`` as a transaction, and ``update()`` installs the
    committed result. ``lock`` serializes those changes with readers on other threads.

    Attributes:
        run: The run's saved details as a mapping.
        account: The account as of the last committed change.
        accepted: Acceptance record for each open order, keyed by order ID.
        last: The financial frontier after the last processed market event.
        events, decisions: The most recent run events and decision summaries (at most 100).
    """

    def __init__(self, operations: Any, profile: str, run_id: str) -> None:
        self.operations, self.profile, self.run_id = operations, profile, run_id
        self.lock = RLock()
        self._checkpoint_time = monotonic()
        self.last: Frontier = {}
        self.ordinal = 0
        self.update(operations.execution_data(profile, run_id))

    def update(self, data: RuntimeData) -> None:
        """Install values read from a committed transaction and restart the idle timer."""
        self.run, self.account, self.accepted, self.last = (
            data.run,
            data.account,
            data.accepted,
            data.last,
        )
        self._snapshot, self.events, self.decisions = data.snapshot, data.events, data.decisions
        self.last.setdefault('ordinal', 0)
        self.last.setdefault('boundary', 0)
        self.ordinal = self.last['ordinal']
        self._checkpoint_time = monotonic()

    def publication(self) -> RuntimeData:
        """Return the current committed values, used as the base for the next change."""
        return RuntimeData(
            self.profile,
            self.run,
            self.account,
            self.accepted,
            self.last,
            self._snapshot,
            self.events,
            self.decisions,
        )

    @property
    def snapshot(self) -> ExecutionSnapshot:
        """The committed account view, stamped with the latest processed market position."""
        return replace(
            self._snapshot,
            time_ns=self.last['market']['time_ns'],
            market_sequence=self.last['market_sequence'],
        )

    def operation_state(self) -> Frontier:
        """The frontier to save with the next operation, numbered after the previous one."""
        state = dict(self.last)
        state['ordinal'] = self.ordinal + 1
        return state

    def can_advance_idle(self, market: MarketSnapshot) -> bool:
        """Prove this run cannot consume liquidity in a certified stable span.

        The reader certifies every intermediate retained book. A crossable
        order always falls back to the original event engine, even if latency
        or another risk condition would currently prevent a fill.
        """
        previous = self.last['market']
        for name in ('bids', 'asks'):
            if tuple(tuple(level) for level in previous[name]) != getattr(market, name):
                return False
        if any(
            previous[name] != getattr(market, name)
            for name in ('trading_state', 'system_event', 'integrity')
        ):
            return False
        for order in self.account.orders:
            if order.status not in OPEN:
                continue
            if order.side == 'buy' and market.asks and order.limit_price >= market.asks[0][0]:
                return False
            if order.side == 'sell' and market.bids and order.limit_price <= market.bids[0][0]:
                return False
        return True

    def advance_idle(self, event: MarketEvent, count: int) -> None:
        """Apply only certified no-fill transitions, preserving logical counters."""
        if (
            count < 1
            or event.sequence != self.last['market_sequence'] + count
            or not self.can_advance_idle(event.snapshot)
        ):
            raise ContractError('unproven idle financial transition')
        self.last = dict(
            self.last,
            tick=self.last['tick'] + count,
            market_sequence=event.sequence,
            event_kind=event.kind,
            source_type=event.source_type,
            market=snapshot_fields(event.snapshot),
            boundary=self.last.get('boundary', 0) + count,
        )
        if monotonic() - self._checkpoint_time >= PERIODIC_CHECKPOINT_SECONDS:
            self.operations.checkpoint_execution(self.profile, self.run_id)

    def advance(self, event: MarketEvent) -> Tuple[Fill, ...]:
        """Process one market event; persist immediately only when it produced fills."""
        if event.snapshot.symbol not in self.run['universe']:
            raise ContractError('market symbol mismatch')
        state, fills = advance(self._snapshot.config, self.account, self.accepted, self.last, event)
        if fills:
            self.operations.execution(
                'persist_fills', self.profile, self.run_id, fills, write=True, runtime_state=state
            )
        else:
            self.last = state
            # Ordinary frames stay in RAM; financial/control commits still save immediately.
            if monotonic() - self._checkpoint_time >= PERIODIC_CHECKPOINT_SECONDS:
                self.operations.checkpoint_execution(self.profile, self.run_id)
        return fills


@dataclass(frozen=True)
class RuntimeData:
    """Detached read data; no lock, owner, operation or ORM session capabilities."""

    profile: str
    run: Dict[str, Any]
    account: AccountSnapshot
    accepted: Dict[str, Any]
    last: Frontier
    snapshot: ExecutionSnapshot
    events: List[Dict[str, Any]]
    decisions: List[Dict[str, Any]]


@dataclass(frozen=True)
class ExecutionChange:
    """What one financial transaction returns: the caller's result and the new read data."""

    result: Any
    publication: Optional[RuntimeData] = None
