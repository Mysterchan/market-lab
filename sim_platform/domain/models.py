"""Immutable public contracts; monetary values are integer USD ten-thousandths."""

from dataclasses import dataclass
from typing import Any, Optional

MONEY_SCALE = 10_000


class ContractError(ValueError):
    """An operation violates a platform domain contract."""


def integer(value: object, name: str, minimum: int = 0) -> None:
    """Reject anything but a plain int (not bool) within [minimum, 2^63-1]."""
    if type(value) is not int or not minimum <= value <= 2**63 - 1:
        raise ContractError(f"{name} must be an integer in [{minimum}, 2^63-1]")


def label(value: object, name: str, maximum: int = 256) -> None:
    """Reject anything but nonblank text of at most ``maximum`` characters."""
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ContractError(f"{name} must be nonempty text (maximum {maximum} characters)")


@dataclass(frozen=True)
class Intent:
    client_id: str
    symbol: str
    side: str
    quantity: int
    limit_price: int

    def validate(self) -> None:
        label(self.client_id, "client_id")
        label(self.symbol, "symbol")
        if self.symbol != self.symbol.strip().upper():
            raise ContractError("symbol must be uppercase without surrounding whitespace")
        if self.side not in ("buy", "sell"):
            raise ContractError("side must be buy or sell")
        integer(self.quantity, "quantity", 1)
        integer(self.limit_price, "limit_price", 1)
        integer(self.quantity * self.limit_price, "notional", 1)


@dataclass(frozen=True)
class MarketOrder:
    """An order to trade at the market; the platform sets its protection price.

    See docs/reference/orders.md: it is accepted as a limit order at the best opposite
    price plus or minus 5%, recorded as a market order.
    """

    client_id: str
    symbol: str
    side: str
    quantity: int

    def validate(self) -> None:
        Intent(self.client_id, self.symbol, self.side, self.quantity, 1).validate()


@dataclass(frozen=True)
class Position:
    symbol: str
    quantity: int
    cost: int  # Remaining acquisition cost including buy fees.


@dataclass(frozen=True)
class OrderSnapshot:
    order_id: str
    intent_id: str
    run_id: str
    symbol: str
    side: str
    quantity: int
    limit_price: int
    filled: int
    status: str


@dataclass(frozen=True)
class AccountSnapshot:
    account_id: str
    profile_id: str
    currency: str
    cash: int
    positions: tuple[Position, ...]
    fees: int
    realized_pnl: int
    sequence: int
    orders: tuple[OrderSnapshot, ...]


@dataclass(frozen=True)
class Profile:
    id: str
    name: str

    def __post_init__(self) -> None:
        label(self.id, "profile ID")
        label(self.name, "profile name")


@dataclass(frozen=True)
class SimulationAccount:
    id: str
    profile_id: str
    name: str
    currency: str

    def __post_init__(self) -> None:
        label(self.id, "account ID")
        label(self.profile_id, "profile ID")
        label(self.name, "account name")
        if self.currency != "USD":
            raise ContractError("Only USD accounts are supported")

    def require_owner(self, profile_id: str) -> None:
        if self.profile_id != profile_id:
            raise ContractError("profile does not own this object")


@dataclass(frozen=True)
class DecisionMaker:
    id: str
    profile_id: str
    name: str
    kind: str
    version: str
    config: object

    def __post_init__(self) -> None:
        label(self.id, "maker ID")
        label(self.profile_id, "profile ID")
        label(self.name, "maker name")
        label(self.version, "maker version")
        if self.kind not in ("human", "rule", "model", "agent"):
            raise ContractError("unknown decision maker kind")

    def require_owner(self, profile_id: str) -> None:
        if self.profile_id != profile_id:
            raise ContractError("profile does not own this object")


@dataclass(frozen=True)
class Run:
    id: str
    account_id: str
    maker_id: str
    status: str
    time_ns: int
    universe: list[str]
    # Caller-supplied JSON. A run configured for execution stores an object here, but
    # the type is not enforced at construction, so readers must check before indexing.
    config: Any
    initial_snapshot: dict
    # The decision journal as JSON; an object once decisions are initialized.
    policy_state: Any

    def __post_init__(self) -> None:
        for value, name in (
            (self.id, "run ID"),
            (self.account_id, "account ID"),
            (self.maker_id, "maker ID"),
        ):
            label(value, name)
        if self.status not in ("running", "completed", "stopped", "failed"):
            raise ContractError("invalid run status")
        integer(self.time_ns, "simulation time")

    def require_active(self) -> None:
        if self.status != "running":
            raise ContractError("run is not active")

    def require_authority(self, maker_id: str) -> None:
        self.require_active()
        if self.maker_id != maker_id:
            raise ContractError("decision maker has no active instruction authority")

    def require_symbol(self, symbol: str) -> None:
        if symbol not in self.universe:
            raise ContractError("symbol outside run universe")


@dataclass(frozen=True)
class LedgerEvent:
    account_id: str
    sequence: int
    event_id: str
    run_id: Optional[str]
    time_ns: Optional[int]
    kind: str
    data: dict

    @classmethod
    def from_record(cls, record: dict) -> "LedgerEvent":
        """Parse the public JSON event format at the application boundary."""
        try:
            return cls(**record)
        except TypeError as exc:
            raise ContractError("invalid ledger event record") from exc

    def __post_init__(self) -> None:
        label(self.account_id, "account ID")
        integer(self.sequence, "event sequence", 1)
        label(self.event_id, "event ID", 1024)
        if self.kind not in (
            "opened",
            "intent",
            "accepted",
            "rejected",
            "fill",
            "cancelled",
            "execution_tick",
            "execution_end",
        ):
            raise ContractError("unknown ledger event kind")
        if self.kind == "opened":
            if self.run_id is not None or self.time_ns is not None:
                raise ContractError("opening event must be outside a run")
        else:
            label(self.run_id, "run ID")
            integer(self.time_ns, "simulation time")
        if not isinstance(self.data, dict):
            raise ContractError("ledger event data must be an object")
