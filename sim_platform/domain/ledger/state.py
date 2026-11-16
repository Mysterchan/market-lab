"""In-memory accounting models. No ORM or persistence dependencies."""

from dataclasses import dataclass, field
from typing import Optional

from sim_platform.domain.models import (
    ContractError,
    Intent,
    OrderSnapshot,
    Position,
    integer,
    label,
)


@dataclass(frozen=True)
class RecordedIntent:
    intent_id: str
    run_id: str
    maker_id: str
    intent: Intent
    status: str = "submitted"


@dataclass(frozen=True)
class Fill:
    order_id: str
    quantity: int
    price: int
    fee: int

    def validate(self) -> None:
        label(self.order_id, "order ID")
        integer(self.quantity, "fill quantity", 1)
        integer(self.price, "fill price", 1)
        integer(self.fee, "fee")
        integer(self.notional, "fill notional", 1)

    @property
    def notional(self) -> int:
        return self.quantity * self.price


@dataclass(frozen=True)
class FillResult:
    """Complete proposed update, validated before any account state changes."""

    cash: int
    position: Position
    fees: int
    realized_pnl: int
    order: OrderSnapshot

    def validate(self) -> None:
        integer(self.cash, "cash")
        integer(self.fees, "fees")
        if self.position.quantity < 0:
            # A short position: negative shares, and its proceeds as a negative cost.
            integer(-self.position.quantity, "short position quantity", 1)
            integer(-self.position.cost, "short position proceeds")
        else:
            integer(self.position.quantity, "position quantity")
            integer(self.position.cost, "position cost")
        if not -(2**63) <= self.realized_pnl <= 2**63 - 1:
            raise ContractError("realized P&L overflow")


@dataclass
class LedgerState:
    """Mutable aggregate containing immutable intent, order and position values."""

    cash: int = 0
    fees: int = 0
    realized_pnl: int = 0
    sequence: int = 0
    account_id: Optional[str] = None
    positions: dict[str, Position] = field(default_factory=dict)
    intents: dict[str, RecordedIntent] = field(default_factory=dict)
    orders: dict[str, OrderSnapshot] = field(default_factory=dict)
    event_ids: set[str] = field(default_factory=set)
    run_times: dict[str, int] = field(default_factory=dict)

    def has_open_orders(self) -> bool:
        return any(order.status in ("accepted", "partial") for order in self.orders.values())

    def has_pending_intents(self, run_id: str) -> bool:
        return any(
            intent.run_id == run_id and intent.status == "submitted"
            for intent in self.intents.values()
        )
