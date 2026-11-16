"""Trusted account test harness; NOT a market-driven execution engine or policy API."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict

from sim_platform.domain.models import label

if TYPE_CHECKING:  # Annotation only.
    from sim_platform.application.platform import Platform


class FixtureExecutor:
    """Writes accept/reject/fill events for a fixture (non-execution) run, for tests.

    Borrows the Platform; it holds only the profile and run identities.
    """

    def __init__(self, platform: Platform, profile_id: str, run_id: str) -> None:
        self._platform = platform
        self._profile = profile_id
        self._run = run_id

    def _record(self, event_id: str, time_ns: int, kind: str, data: Dict[str, Any]) -> None:
        self._platform._fixture_event(self._profile, self._run, event_id, time_ns, kind, data)

    def accept(self, intent_id: str, order_id: str, time_ns: int) -> None:
        label(order_id, "order ID")
        self._record(
            f"accepted:{order_id}",
            time_ns,
            "accepted",
            {"intent_id": intent_id, "order_id": order_id},
        )

    def reject(self, intent_id: str, reason: str, time_ns: int) -> None:
        label(reason, "rejection reason")
        self._record(
            f"rejected:{intent_id}", time_ns, "rejected", {"intent_id": intent_id, "reason": reason}
        )

    def fill(
        self, fill_id: str, order_id: str, quantity: int, price: int, fee: int, time_ns: int
    ) -> None:
        label(fill_id, "fill ID")
        self._record(
            f"fill:{fill_id}",
            time_ns,
            "fill",
            {"order_id": order_id, "quantity": quantity, "price": price, "fee": fee},
        )
