"""Execution example: genuine market-driven fills in temporary accounts, no FixtureExecutor."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Dict, Optional, Union

from sim_platform import Platform, Intent
from sim_platform.execution import ExecutionConfig
from .market_demo import write_fixture

# Starting cash and fee per share for the demo account (money units).
ACCOUNT_CASH = 100_000_000
FEE_PER_SHARE = 100


def run_execution_demo(directory: Optional[Union[str, Path]] = None) -> Dict[str, Any]:
    """Trade against the fixture's displayed book and check the resulting account."""
    if directory is None:
        with TemporaryDirectory(prefix='trading-execution-demo-') as temporary:
            result = run_execution_demo(Path(temporary))
        result['temporary_data_removed'] = True
        return result
    directory = Path(directory)
    source, _ = write_fixture(directory / 'fixture')
    with Platform(directory / 'accounts') as app, app.open_market('AAPL') as market:
        profile = app.create_profile('Execution demo')
        account = app.create_account(profile, 'AAPL simulation', ACCOUNT_CASH)
        maker = app.create_maker(profile, 'CLI human', 'human', 'human')
        run = app.create_execution_run(
            profile, account, maker, 'AAPL', ExecutionConfig(fee_per_share=FEE_PER_SHARE)
        )
        market.load(source)
        with app.execution_session(profile, run, maker, market) as execution:
            for _ in range(3):
                execution.step()
            rejected = execution.submit(Intent('too-large', 'AAPL', 'buy', 1000, 1_901_000))
            bought = execution.submit(Intent('buy', 'AAPL', 'buy', 50, 1_901_000))
            assert execution.snapshot.account.positions == ()  # submission never fills
            execution.step()  # later E event: displayed ask supplies 50 shares
            first = asdict(execution.snapshot)
            sold = execution.submit(Intent('sell', 'AAPL', 'sell', 20, 1_900_000))
            execution.step()  # P trade does not re-use book or trigger fills
            assert execution.last_fills == ()
            execution.step()  # B also does not trigger a simulated fill
            execution.step()  # D book event: displayed bid supplies sale
            final = execution.snapshot.account
            assert (final.cash, final.fees, final.realized_pnl) == (42_943_000, 7_000, -24_000)
            assert final.positions[0].quantity == 30 and final.positions[0].cost == 57_033_000
            execution.step()  # explicit EOF -> complete
            events = app.events(profile, account)
            assert app.replay_snapshot(profile, account, events) == execution.snapshot.account
            return dict(
                result='passed',
                model=asdict(execution.snapshot.config),
                rejected=asdict(rejected),
                buy=asdict(bought),
                sell=asdict(sold),
                after_buy=first,
                final=asdict(execution.snapshot),
                events=events,
            )
