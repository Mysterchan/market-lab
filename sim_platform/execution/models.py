"""Finite, deterministic execution model; integer USD ten-thousandths."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

from sim_platform.domain.models import AccountSnapshot, ContractError, Run, integer

# One displayed price level from the historical book: (price, displayed shares).
Level = Tuple[int, int]
# One level of the simulation's supply budget: [price, last displayed shares, shares
# still available to simulated orders]. A list, because a proposed transaction consumes it.
BudgetLevel = List[int]
# The saved financial frontier after the last processed market event: tick, market
# sequence, the market snapshot as a mapping, and the bid/ask supply budgets.
Frontier = Dict[str, Any]
# One simulated fill as recorded in a run event: order_id, quantity, price, fee,
# model, market_sequence and tick.
Fill = Dict[str, Any]


# Matching rules. Both take displayed liquidity when an order's limit reaches the
# opposite best price; the queue model also fills resting orders from executions at
# their own price once the displayed shares ahead of them have traded.
DISPLAYED_LIMIT = 'displayed-limit-v1'
DISPLAYED_LIMIT_QUEUE = 'displayed-limit-queue-v1'
EXECUTION_MODELS = (DISPLAYED_LIMIT, DISPLAYED_LIMIT_QUEUE)
# Upper bound of the impact coefficient: 10,000 basis points is 100% of the price.
MAX_IMPACT_BPS = 10_000
# A lognormal delay is capped at this multiple of its median, so eligibility stays bounded.
MAX_LATENCY_MULTIPLE = 1_000
# Sigma of the lognormal latency in thousandths; 3000 (sigma 3) is already extreme.
MAX_LATENCY_SIGMA_MILLI = 3_000


@dataclass(frozen=True)
class ExecutionConfig:
    """Rules for accepting and filling simulated orders in one run.

    ``model`` selects the matching rule (``EXECUTION_MODELS``). ``latency_ns`` delays
    when an accepted order may first match; ``latency_jitter_ns`` adds a further delay
    drawn uniformly from 0 to that value, fixed by the order's ID so every replay sees
    the same delay. ``latency_sigma_milli`` above 0 instead draws a lognormal delay with
    median ``latency_ns`` and sigma ``latency_sigma_milli / 1000`` (heavy right tail, as
    measured network and queueing delays have), also fixed by the order ID and capped at
    1,000 times the median. ``fee_per_share`` is charged on every filled share and reserved in
    advance for buys. ``impact_bps`` is the square-root market impact coefficient: a
    fill taking a fraction f of the displayed opposite side pays ``impact_bps * sqrt(f)``
    basis points worse than the quoted price, never beyond the order's limit.
    ``borrow_bps_per_year`` charges short positions an annual borrow rate on their sale
    proceeds for the market time they were open; it is added to the fee of the buy that
    covers them, and the fill records it as ``borrow_fee``.
    """

    model: str = DISPLAYED_LIMIT
    latency_ns: int = 0
    fee_per_share: int = 0
    max_order_quantity: int = 1_000_000
    latency_jitter_ns: int = 0
    impact_bps: int = 0
    latency_sigma_milli: int = 0
    borrow_bps_per_year: int = 0

    def __post_init__(self) -> None:
        if self.model not in EXECUTION_MODELS:
            raise ContractError('unsupported execution model')
        integer(self.latency_ns, 'latency')
        integer(self.fee_per_share, 'fee per share')
        integer(self.max_order_quantity, 'maximum order quantity', 1)
        integer(self.latency_jitter_ns, 'latency jitter')
        integer(self.impact_bps, 'impact basis points')
        if self.impact_bps > MAX_IMPACT_BPS:
            raise ContractError('impact basis points must be at most 10000')
        integer(self.borrow_bps_per_year, 'borrow basis points per year')
        if self.borrow_bps_per_year > MAX_IMPACT_BPS:
            raise ContractError('borrow basis points per year must be at most 10000')
        integer(self.latency_sigma_milli, 'latency sigma')
        if self.latency_sigma_milli > MAX_LATENCY_SIGMA_MILLI:
            raise ContractError('latency sigma must be at most 3000 thousandths')
        if self.latency_sigma_milli and self.latency_jitter_ns:
            raise ContractError('choose either uniform jitter or a lognormal latency')

    @property
    def max_delay_ns(self) -> int:
        """The longest delay an order can draw, for range checks at acceptance."""
        if self.latency_sigma_milli:
            return self.latency_ns * MAX_LATENCY_MULTIPLE
        return self.latency_ns + self.latency_jitter_ns


@dataclass(frozen=True)
class OrderResult:
    """Outcome of submitting one intent; ``reason`` explains a rejection."""

    intent_id: str
    order_id: str | None
    status: str
    reason: str | None = None


@dataclass(frozen=True)
class ExecutionSnapshot:
    """Read-only view of a run's account, including cash and shares held for open orders."""

    run_id: str
    status: str
    time_ns: int
    market_sequence: int
    account: AccountSnapshot
    reserved_cash: int
    available_cash: int
    reserved_shares: tuple[tuple[str, int], ...]
    config: ExecutionConfig


@dataclass(frozen=True)
class ExecutionContext:
    """Transaction-local inputs, not a UI snapshot or a transferable capability.

    ``state`` is the financial frontier including the last processed market event.
    Named fields let decision and handoff code request only the values it uses.
    """

    run: Run
    config: ExecutionConfig
    account: AccountSnapshot
    state: Frontier
