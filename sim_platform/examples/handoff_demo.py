"""Human-to-rule handoff, preserving account/orders and replaying the actions."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple, Union

from sim_platform import Platform, Intent
from sim_platform.decisions import (
    HumanPolicy,
    HoldThenSell,
    RecordedPolicy,
    DecisionConfig,
    StaleAuthority,
    submit,
)
from sim_platform.execution import ExecutionConfig
from sim_platform.market import load_news
from .market_demo import write_fixture

if TYPE_CHECKING:  # Annotation only.
    from sim_platform.domain.models import AccountSnapshot

# Starting cash and fee per share for each demo account (money units).
ACCOUNT_CASH = 100_000_000
FEE_PER_SHARE = 100
# Expected final cash, fees and realized P&L, and the handoff's market boundary.
EXPECTED_CASH = 99_988_000
EXPECTED_FEES = 2000
EXPECTED_REALIZED_PNL = -12000
EXPECTED_HANDOFF_BOUNDARY = 5


def run_handoff_demo(
    directory: Optional[Union[str, Path]] = None, library: Optional[Union[str, Path]] = None
) -> Dict[str, Any]:
    """Run a human-to-rule handoff, replay its records, and check both runs agree."""
    if directory is None:
        with TemporaryDirectory(prefix='trading-handoff-demo-') as temporary:
            result = run_handoff_demo(Path(temporary), library)
        result['temporary_data_removed'] = True
        return result
    directory = Path(directory)
    source, news = write_fixture(directory / 'fixture')
    with Platform(directory / 'accounts') as app:
        profile = app.create_profile('Handoff demo')

        def run_once(
            recording: Optional[List[Dict[str, Any]]] = None,
        ) -> Tuple[AccountSnapshot, Dict[str, Any]]:
            account = app.create_account(profile, 'Handoff account', ACCOUNT_CASH)
            human: Any = (
                HumanPolicy()
                if recording is None
                else RecordedPolicy([r for r in recording if r['generation'] == 0], kind='human')
            )
            rule: Any = (
                HoldThenSell()
                if recording is None
                else RecordedPolicy([r for r in recording if r['generation'] == 1])
            )
            first = app.create_maker(profile, human.name, human.kind, human.version)
            second = app.create_maker(profile, rule.name, rule.kind, rule.version)
            run = app.create_decision_run(
                profile,
                account,
                first,
                'AAPL',
                human,
                DecisionConfig(mode='manual'),
                ExecutionConfig(fee_per_share=FEE_PER_SHARE),
            )
            with app.open_market('AAPL', library=library) as market:
                market.load(source, news=load_news(news))
                with app.decision_runner(profile, run, first, market, human) as runner:
                    for _ in range(3):
                        runner.step()
                    if recording is None:
                        runner.act(
                            [
                                submit(Intent('human-buy', 'AAPL', 'buy', 10, 1_901_000)),
                                submit(Intent('human-passive', 'AAPL', 'buy', 1, 1)),
                            ]
                        )
                    else:
                        runner.decide()
                    runner.step()  # announcement
                    runner.step()  # E: human buy fills, passive order remains open
                    runner.pause()
                    old = runner.controller()
                    delayed = old.prepare()
                    before = runner.snapshot.account
                    runner.handoff(second, rule, reason='human delegates holding management')
                    assert runner.snapshot.account == before
                    try:
                        runner.respond(delayed, {'actions': [], 'state': {}})
                    except StaleAuthority:
                        pass
                    else:
                        raise AssertionError('late response must be rejected')
                    runner.play()
                    while runner.snapshot.status == 'running':
                        runner.pump()
                    final = runner.snapshot.account
                    journal = app.run(profile, run)['policy_state']
            assert app.replay_snapshot(profile, account, app.events(profile, account)) == final
            if recording is not None:
                human.assert_consumed()
                rule.assert_consumed()
            return final, journal

        final, journal = run_once()
        replay_final, replay_journal = run_once(journal['records'])

        def fields(a: AccountSnapshot) -> Tuple[Any, ...]:
            return (
                a.cash,
                a.positions,
                a.fees,
                a.realized_pnl,
                tuple((o.side, o.quantity, o.filled, o.status) for o in a.orders),
            )

        assert fields(final) == fields(replay_final)
        assert (final.cash, final.positions, final.fees, final.realized_pnl) == (
            EXPECTED_CASH,
            (),
            EXPECTED_FEES,
            EXPECTED_REALIZED_PNL,
        )
        assert [r['fingerprint'] for r in journal['records']] == [
            r['fingerprint'] for r in replay_journal['records']
        ]
        assert (
            journal['handoffs'][0]['boundary']
            == replay_journal['handoffs'][0]['boundary']
            == EXPECTED_HANDOFF_BOUNDARY
        )
        return dict(
            result='passed',
            final=asdict(final),
            handoffs=journal['handoffs'],
            records=journal['records'],
            authority_rejections=journal['authority_rejections'],
            recorded_replay_equal=True,
            existing_orders_preserved=True,
        )
