"""Owner-only read capture; detached input for workers, no validation or formatting."""

from __future__ import annotations

from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Dict, Iterable, Mapping, Tuple

if TYPE_CHECKING:  # Annotation only.
    from sim_platform.domain.models import OrderSnapshot
    from sim_platform.execution.models import ExecutionSnapshot
    from sim_platform.market.models import MarketSnapshot


@dataclass(frozen=True)
class CapturedRead:
    """Detached values for one read tool, captured on the owner and formatted in the child."""

    request: dict
    position: Mapping[str, str]
    market: MarketSnapshot
    execution: ExecutionSnapshot
    history: Any
    order_versions: Mapping[str, int]
    receipt_index: str


class OrderVersions:
    """Owner-maintained observable order versions, independent of account sequence.

    One per Agent run, held by its tool capture; a version increases each time an
    order's snapshot changes.
    """

    def __init__(self) -> None:
        self.values: Dict[str, Tuple[OrderSnapshot, int]] = {}

    def observe(self, orders: Iterable[OrderSnapshot]) -> Dict[str, int]:
        """Record changes and return the current version of each given order."""
        for value in orders:
            previous, version = self.values.get(value.order_id, (None, 0))
            if previous != value:
                self.values[value.order_id] = value, version + 1
        return {value.order_id: self.values[value.order_id][1] for value in orders}


def capture_read(
    request: Dict[str, Any], position: Mapping[str, str], capture: Any, receipt_index: str
) -> CapturedRead:
    """Capture an already-validated request at the owner-assigned service boundary."""
    values = (
        capture.capture_at(request['tool'], position)
        if hasattr(capture, 'capture_at')
        else capture(request['tool'])
    )
    execution = values['execution']
    tool = request['tool']
    account_needed = tool in ('account.snapshot', 'state.snapshot')
    orders_needed = tool in ('orders.list', 'orders.get', 'state.snapshot')
    selected_account = replace(
        execution.account,
        orders=execution.account.orders if orders_needed else (),
        positions=execution.account.positions if account_needed else (),
    )
    execution = replace(
        execution,
        account=selected_account,
        reserved_shares=execution.reserved_shares if account_needed else (),
    )
    return CapturedRead(
        request,
        MappingProxyType(dict(position)),
        values['market'],
        execution,
        values['history'],
        MappingProxyType(dict(values['order_versions'])) if orders_needed else MappingProxyType({}),
        receipt_index,
    )
