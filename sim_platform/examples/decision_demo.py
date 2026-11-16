"""Decision example: rule execution and independent recorded-action replay."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Dict, List, Optional, Tuple, Union

from sim_platform import Platform
from sim_platform.decisions import BuyHoldSell, RecordedPolicy
from sim_platform.execution import ExecutionConfig
from sim_platform.market import load_news
from .market_demo import write_fixture

# Starting cash for each demo account and the fee per share, in money units.
ACCOUNT_CASH = 100_000_000
FEE_PER_SHARE = 100
# The rule's expected result: cash, fees and realized P&L (money units).
EXPECTED_CASH = 99_988_000
EXPECTED_FEES = 2000
EXPECTED_REALIZED_PNL = -12000


def run_decision_demo(
    directory: Optional[Union[str, Path]] = None, library: Optional[Union[str, Path]] = None
) -> Dict[str, Any]:
    """Run the rule, replay its recorded actions on a new account, and compare results."""
    if directory is None:
        with TemporaryDirectory(prefix='trading-decision-demo-') as temporary:
            result = run_decision_demo(Path(temporary), library)
        result['temporary_data_removed'] = True
        return result
    directory = Path(directory)
    source, news = write_fixture(directory / 'fixture')
    with Platform(directory / 'accounts') as app:
        profile = app.create_profile('Decision demo')

        def run_policy(policy: Any, name: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
            account = app.create_account(profile, name, ACCOUNT_CASH)
            maker = app.create_maker(profile, policy.name, policy.kind, policy.version)
            run = app.create_decision_run(
                profile,
                account,
                maker,
                'AAPL',
                policy,
                execution_config=ExecutionConfig(fee_per_share=FEE_PER_SHARE),
            )
            with app.open_market('AAPL', library=library) as market:
                market.load(source, news=load_news(news))
                with app.decision_runner(profile, run, maker, market, policy) as runner:
                    while runner.snapshot.status == 'running':
                        runner.step()
                    result = asdict(runner.snapshot.account)
                    records = runner.records
                assert app.replay_snapshot(
                    profile, account, app.events(profile, account)
                ) == app.snapshot(profile, account)
            return result, records

        final, records = run_policy(BuyHoldSell(), 'Rule account')
        replay = RecordedPolicy(records)
        replay_final, replay_records = run_policy(replay, 'Recorded replay account')
        replay.assert_consumed()

        def summary(account: Dict[str, Any]) -> Dict[str, Any]:
            return {key: account[key] for key in ('cash', 'positions', 'fees', 'realized_pnl')}

        assert summary(final) == summary(replay_final)
        assert (final['cash'], final['fees'], final['realized_pnl'], final['positions']) == (
            EXPECTED_CASH,
            EXPECTED_FEES,
            EXPECTED_REALIZED_PNL,
            (),
        )
        return {
            'result': 'passed',
            'rule': 'buy 10 AAPL, hold 2 simulated seconds after observed fill, sell',
            'final': final,
            'records': records,
            'recorded_replay_equal': True,
            'decision_count': len(records),
        }
