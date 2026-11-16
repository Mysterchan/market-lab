"""Rejected events must leave the same in-memory ledger safe to reuse."""

import copy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from sim_platform.domain.ledger import apply_event, empty_state
from sim_platform.domain.models import ContractError, LedgerEvent


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.state = empty_state()
        apply_event(
            self.state, LedgerEvent("account", 1, "opening", None, None, "opened", {"cash": 1000})
        )

    def event(self, kind, data, event_id=None):
        return LedgerEvent(
            "account",
            self.state.sequence + 1,
            event_id or f"event-{self.state.sequence + 1}",
            "run",
            self.state.sequence + 1,
            kind,
            data,
        )

    def submit(self, client_id, side="buy", quantity=10, price=10):
        apply_event(
            self.state,
            self.event(
                "intent",
                {
                    "intent_id": client_id,
                    "run_id": "run",
                    "maker_id": "maker",
                    "intent": {
                        "client_id": client_id,
                        "symbol": "AAPL",
                        "side": side,
                        "quantity": quantity,
                        "limit_price": price,
                    },
                },
            ),
        )

    def accept(self, intent_id, order_id):
        apply_event(
            self.state, self.event("accepted", {"intent_id": intent_id, "order_id": order_id})
        )

    def assert_rejected_unchanged(self, event):
        before = copy.deepcopy(self.state)
        payload = copy.deepcopy(event.data)
        with self.assertRaises(ContractError):
            apply_event(self.state, event)
        self.assertEqual(self.state, before)
        self.assertEqual(event.data, payload)

    def test_invalid_buy_fills_leave_same_state_reusable(self):
        self.submit("buy", quantity=200, price=10)
        self.accept("buy", "order")
        for quantity, price, fee, order in [
            (201, 10, 0, "order"),
            (101, 10, 0, "order"),
            (1, 11, 0, "order"),
            (1, 10, -1, "order"),
            (0, 10, 0, "order"),
            (1, 10, 0, "unknown"),
        ]:
            self.assert_rejected_unchanged(
                self.event(
                    "fill", {"order_id": order, "quantity": quantity, "price": price, "fee": fee}
                )
            )
        apply_event(
            self.state,
            self.event("fill", {"order_id": "order", "quantity": 10, "price": 10, "fee": 1}),
        )
        self.assertEqual(self.state.cash, 899)
        self.assertEqual(self.state.positions["AAPL"].quantity, 10)
        self.assertEqual(self.state.positions["AAPL"].cost, 101)
        self.assertEqual(self.state.orders["order"].status, "partial")

    def test_sell_failures_leave_same_state_reusable(self):
        self.submit("buy")
        self.accept("buy", "buy-order")
        apply_event(
            self.state,
            self.event("fill", {"order_id": "buy-order", "quantity": 10, "price": 10, "fee": 1}),
        )
        self.submit("sell", "sell", 11, 20)
        self.accept("sell", "sell-order")
        for quantity, price, fee in [(11, 20, 0), (4, 19, 0), (4, 20, 1000)]:
            self.assert_rejected_unchanged(
                self.event(
                    "fill",
                    {"order_id": "sell-order", "quantity": quantity, "price": price, "fee": fee},
                )
            )
        apply_event(
            self.state,
            self.event("fill", {"order_id": "sell-order", "quantity": 4, "price": 20, "fee": 1}),
        )
        self.assertEqual((self.state.cash, self.state.realized_pnl, self.state.fees), (978, 39, 2))
        self.assertEqual(
            (self.state.positions["AAPL"].quantity, self.state.positions["AAPL"].cost), (6, 61)
        )

    def test_overflow_after_calculation_does_not_modify_account(self):
        self.submit("buy")
        self.accept("buy", "order")
        self.state.fees = 2**63 - 1  # Finite boundary without billions of fixture events.
        self.assert_rejected_unchanged(
            self.event("fill", {"order_id": "order", "quantity": 1, "price": 10, "fee": 1})
        )
        self.assertEqual(self.state.positions, {})

    def test_duplicate_order_does_not_resolve_second_intent(self):
        self.submit("first")
        self.accept("first", "order")
        self.submit("second")
        self.assert_rejected_unchanged(
            self.event("accepted", {"intent_id": "second", "order_id": "order"})
        )
        self.assertEqual(self.state.intents["second"].status, "submitted")
        self.accept("second", "other-order")
        self.assertEqual(self.state.intents["second"].status, "accepted")

    def test_bad_payload_and_history_do_not_advance_ledger(self):
        for event in [
            self.event("intent", {}),
            self.event("fill", {}),
            LedgerEvent("account", 2, "opening", "run", 2, "intent", {}),
            LedgerEvent("other", 2, "x", "run", 2, "intent", {}),
            LedgerEvent("account", 3, "x", "run", 3, "intent", {}),
        ]:
            self.assert_rejected_unchanged(event)
        self.submit("valid")
        self.assert_rejected_unchanged(
            LedgerEvent(
                "account", 3, "past", "run", 0, "rejected", {"intent_id": "valid", "reason": "past"}
            )
        )
        self.assert_rejected_unchanged(self.event("rejected", {"intent_id": "valid", "reason": ""}))
        apply_event(self.state, self.event("rejected", {"intent_id": "valid", "reason": "fixture"}))
        self.assertEqual(self.state.intents["valid"].status, "rejected")


if __name__ == "__main__":
    unittest.main()
