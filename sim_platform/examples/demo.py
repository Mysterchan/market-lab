"""Fixed, independently calculable account scenario."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Union

from sim_platform.application.platform import Platform
from sim_platform.examples.fixture_executor import FixtureExecutor
from sim_platform.domain.models import Intent, MONEY_SCALE


def run_demo(directory: Union[str, Path]) -> dict:
    """Run the fixed account scenario, checking every amount, and return its results."""
    unit = MONEY_SCALE
    with Platform(directory) as app:
        profile = app.create_profile("Account demo")
        account = app.create_account(profile, "Account A", 10_000 * unit)
        other = app.create_account(profile, "Account B", 10_000 * unit)
        maker = app.create_maker(profile, "Manual fixture policy", "human", "1")
        run = app.create_run(profile, account, maker, ["AAPL"], {"scenario": "fixed-account-demo"})
        decision = app.decision_session(profile, run, maker)
        executor = FixtureExecutor(app, profile, run)
        buy = decision.submit(Intent("buy-1", "AAPL", "buy", 10, 100 * unit), 10)
        after_intent = asdict(decision.observe())
        if after_intent["cash"] != 10_000 * unit or after_intent["positions"]:
            raise AssertionError("intent mutated account")
        executor.accept(buy, "buy-order", 11)
        executor.fill("buy-fill", "buy-order", 10, 100 * unit, unit, 12)
        after_buy = decision.observe()
        executor.fill("buy-fill", "buy-order", 10, 100 * unit, unit, 12)
        if decision.observe() != after_buy:
            raise AssertionError("duplicate fill changed account")
        sell = decision.submit(Intent("sell-1", "AAPL", "sell", 4, 110 * unit), 20)
        executor.accept(sell, "sell-order", 21)
        executor.fill("sell-fill", "sell-order", 4, 110 * unit, unit, 22)
        decision.save_state({"actions": 2})
        final = decision.observe()
        if (final.cash, final.positions[0].quantity, final.fees, final.realized_pnl) != (
            9438 * unit,
            6,
            2 * unit,
            386_000,
        ):
            raise AssertionError("account disagrees with independent expected amounts")
        if app.snapshot(profile, other).cash != 10_000 * unit:
            raise AssertionError("account isolation failed")
        events = app.events(profile, account)
        if app.replay_snapshot(profile, account, events) != final:
            raise AssertionError("recorded-event replay mismatch")
        app.finish_run(profile, run)
    with Platform(directory) as reopened:
        if reopened.snapshot(profile, account) != final:
            raise AssertionError("reopen mismatch")
        if reopened.run(profile, run)["policy_state"] != {"actions": 2}:
            raise AssertionError("policy state not saved")
    return {
        "scenario": "fixed-account-demo",
        "result": "passed",
        "money_scale": unit,
        "profile_id": profile,
        "account_id": account,
        "other_account_id": other,
        "run_id": run,
        "after_intent": after_intent,
        "final": asdict(final),
        "checks": [
            "intent is not fill",
            "fixed cash/quantity/fee/P&L",
            "duplicate fill",
            "account isolation",
            "event replay",
            "reopen",
            "policy state",
        ],
        "data_directory": str(directory),
    }
