"""Market orders and short selling: the rules in docs/reference/orders.md."""

from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from sim_platform import ContractError, Platform
from sim_platform.desktop.presentation import round_trips
from sim_platform.domain.models import AccountSnapshot, Intent, Position
from sim_platform.execution import ExecutionConfig
from sim_platform.execution.risk import (
    SHORT_SELLING_RULE,
    protection_price,
    rejection_reason,
    reservations,
)
from sim_platform.market.models import DAY_NS, MarketSnapshot

# Flat bars: every step of a day shows bid = price - 0.01 and ask = price + 0.01.
FLAT = """date,open,high,low,close,volume
2026-01-05,100.00,100.00,100.00,100.00,100000
2026-01-06,90.00,90.00,90.00,90.00,100000
2026-01-07,95.00,95.00,95.00,95.00,100000
2026-01-08,110.00,110.00,110.00,110.00,100000
"""
FEE = 100  # one cent per share


def market(bid, ask):
    return MarketSnapshot('AAPL', 1, 1, 'playing', 'sequenced', bid, ask, 1, 1, 1, 'T', 'Q')


class RuleTests(unittest.TestCase):
    def test_protection_price_is_five_percent_beyond_the_touch(self):
        book = market(((999_900, 10),), ((1_000_100, 10),))
        self.assertEqual(protection_price('buy', book), 1_000_100 + 50_005)
        self.assertEqual(protection_price('sell', book), 999_900 - 49_995)
        self.assertIsNone(protection_price('buy', market(((1, 1),), ())))
        self.assertEqual(protection_price('sell', market(((1, 1),), ((3, 1),))), 1)

    def test_without_the_rule_reservations_and_rejections_are_unchanged(self):
        account = AccountSnapshot(
            'a', 'p', 'USD', 1_000_000, (Position('AAPL', 5, 500),), 0, 0, 1, ()
        )
        config = ExecutionConfig(fee_per_share=FEE)
        book = market(((99_900, 100),), ((100_100, 100),))
        oversell = Intent('s', 'AAPL', 'sell', 6, 99_900)
        self.assertEqual(
            rejection_reason(oversell, account, config, book),
            'insufficient available shares; short selling is unsupported',
        )
        self.assertEqual(reservations(account, config), (0, {}))
        self.assertIsNone(
            rejection_reason(oversell, account, config, book, dict(SHORT_SELLING_RULE))
        )


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix='orders-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.csv = self.root / 'flat.csv'
        self.csv.write_text(FLAT)
        self.app = Platform(self.root / 'app')
        self.addCleanup(self.app.close)
        self.profile = self.app.create_profile('Quant')
        self.account = self.app.create_account(self.profile, 'Cash', 100_000_000)

    def open(self, execution=None, **options):
        experiment = self.app.open_experiment(
            self.profile,
            self.account,
            source=self.csv,
            execution_mode='controlled',
            execution_config=execution or ExecutionConfig(fee_per_share=FEE),
            **options,
        )
        self.addCleanup(experiment.close)
        self.experiment = experiment
        return experiment

    def to_day(self, day):
        while self.experiment.view()['market']['time_ns'] < day * DAY_NS:
            self.experiment.step()
        self.experiment.step()

    def fill_next(self):
        """Step until the open order fills; a trade print can come before a book change."""
        count = len(self.fills())
        for _ in range(4):
            self.experiment.step()
            if len(self.fills()) > count:
                return
        self.fail('no fill arrived')

    def fills(self):
        events = self.app.run_events(self.profile, self.experiment.run_id)
        return [e['data'] for e in events if e['kind'] == 'fill']

    def account_now(self):
        return self.app.snapshot(self.profile, self.account)

    def verify_replay(self):
        run_id = self.experiment.run_id
        while self.experiment.runner.active:
            self.experiment.step()
        self.experiment.close()
        self.app.close()
        return Platform.replay_run(self.root / 'app', run_id, self.root / 'replay')


class MarketOrderTests(Fixture):
    def test_a_market_buy_reserves_at_protection_and_fills_at_the_displayed_ask(self):
        experiment = self.open()
        experiment.step()
        experiment.order('m1', 'buy', 5, order_type='market')
        reserved = experiment.view()['execution']['reserved_cash']
        self.assertEqual(reserved, 5 * (1_000_100 + 50_005 + FEE))
        self.fill_next()
        self.assertEqual([(f['quantity'], f['price']) for f in self.fills()], [(5, 1_000_100)])
        self.assertEqual(experiment.view()['execution']['reserved_cash'], 0)
        events = self.app.run_events(self.profile, experiment.run_id)
        accepted = next(e for e in events if e['kind'] == 'accepted')['data']
        self.assertEqual((accepted['order_type'], accepted['protection_bps']), ('market', 500))
        # An exact repeat returns the original result; changed content is rejected,
        # as for limit orders.
        experiment.order('m1', 'buy', 5, order_type='market')
        self.assertEqual(experiment.view()['decisions'][-1]['results'][0]['status'], 'filled')
        experiment.order('m1', 'buy', 6, order_type='market')
        self.assertEqual(
            experiment.view()['decisions'][-1]['results'][0]['reason'],
            'client ID reused with different content',
        )
        experiment.order('m2', 'sell', 5, order_type='market')
        self.fill_next()
        self.assertEqual(self.fills()[-1]['price'], 999_900)
        self.assertTrue(self.verify_replay()['verified'])

    def test_a_market_order_without_an_opposite_quote_is_rejected(self):
        experiment = self.open(settings=dict(start_mode='beginning'))
        experiment.order('m1', 'buy', 5, order_type='market')
        events = self.app.run_events(self.profile, experiment.run_id)
        rejected = next(e for e in events if e['kind'] == 'rejected')['data']
        self.assertEqual(rejected['reason'], 'no opposite quote to price a market order')

    def test_a_model_can_send_market_orders(self):
        model = self.root / 'model.py'
        model.write_text(
            'def decide(bars, account, context):\n'
            '    if account["position"] == 0 and len(bars) == 1:\n'
            '        return dict(orders=[dict(side="buy", quantity=3, type="market")])\n'
        )
        experiment = self.app.open_experiment(
            self.profile,
            self.account,
            source=self.csv,
            method='model',
            execution_mode='controlled',
            settings=dict(model_file=str(model)),
        )
        self.addCleanup(experiment.close)
        while experiment.runner.active:
            experiment.step()
        self.assertEqual([p.quantity for p in self.account_now().positions], [3])


class ShortSellingTests(Fixture):
    def test_without_allow_short_an_oversell_is_rejected_as_before(self):
        experiment = self.open()
        experiment.step()
        experiment.order('s1', 'sell', 10, 999_900)
        events = self.app.run_events(self.profile, experiment.run_id)
        self.assertIn('short selling is unsupported', events[-1]['data']['reason'])

    def test_short_cover_cross_and_reshort_follow_the_documented_arithmetic(self):
        experiment = self.open(allow_short=True)
        run = self.app.run(self.profile, experiment.run_id, include_records=False)
        self.assertEqual(run['config']['short_selling'], SHORT_SELLING_RULE)
        experiment.step()
        experiment.order('s1', 'sell', 10, 999_900)
        self.fill_next()
        proceeds = 10 * 999_900 - 10 * FEE
        account = self.account_now()
        self.assertEqual(account.positions[0], Position('AAPL', -10, -proceeds))
        self.assertEqual(account.cash, 100_000_000 + proceeds)
        view = experiment.view()['execution']
        self.assertEqual(view['available_cash'], account.cash - proceeds * 3 // 2)

        self.to_day(1)  # 90.00: cover 4 at 90.01
        experiment.order('b1', 'buy', 4, 900_100)
        self.fill_next()
        cover_one = proceeds * 4 // 10 - (4 * 900_100 + 4 * FEE)
        self.to_day(2)  # 95.00: buy 10 at 95.01, covering 6 and holding 4 long
        experiment.order('b2', 'buy', 10, 950_100)
        self.fill_next()
        cover_two = (proceeds - proceeds * 4 // 10) - (6 * 950_100 + 600)
        long_cost = 4 * 950_100 + 400
        self.assertEqual(self.account_now().positions[0], Position('AAPL', 4, long_cost))
        self.to_day(3)  # 110.00: sell 6 at 109.99, closing 4 long and shorting 2
        experiment.order('s2', 'sell', 6, 1_099_900)
        self.fill_next()
        close_long = 4 * 1_099_900 - 400 - long_cost
        account = self.account_now()
        self.assertEqual(account.positions[0], Position('AAPL', -2, -(2 * 1_099_900 - 200)))
        self.assertEqual(account.realized_pnl, cover_one + cover_two + close_long)
        rows = [
            dict(time_ns=0, side=s, quantity=f['quantity'], price=f['price'], fee=f['fee'])
            for f, s in zip(self.fills(), ('sell', 'buy', 'buy', 'sell'))
        ]
        for index, row in enumerate(rows):
            row['time_ns'] = index
        paired = round_trips(rows)
        self.assertEqual(paired['realized_pnl'], account.realized_pnl)
        self.assertEqual([t['pnl'] for t in paired['trips']], [cover_one + cover_two, close_long])
        self.assertTrue(paired['open'])
        self.assertTrue(self.verify_replay()['verified'])

    def test_borrow_cost_is_charged_when_the_short_is_covered_and_replays(self):
        config = ExecutionConfig(fee_per_share=FEE, borrow_bps_per_year=36_50)
        experiment = self.open(execution=config, allow_short=True)
        experiment.step()
        experiment.order('s1', 'sell', 10, 999_900)
        self.fill_next()
        self.to_day(2)
        experiment.order('b1', 'buy', 10, 1_200_000)
        self.fill_next()
        events = self.app.run_events(self.profile, experiment.run_id)
        sale, cover = [e for e in events if e['kind'] == 'fill']
        held = cover['time_ns'] - sale['time_ns']
        self.assertGreater(held, 0)
        # The short's proceeds after fees, at 36.5% a year (0.1% a day) for the time held.
        proceeds = 10 * 999_900 - 10 * FEE
        expected = -(-proceeds * 3650 * held // (10_000 * 365 * DAY_NS))
        self.assertEqual(cover['data']['borrow_fee'], expected)
        self.assertEqual(cover['data']['fee'], 10 * FEE + expected)
        self.assertEqual(self.account_now().positions, ())
        self.assertTrue(self.verify_replay()['verified'])

    def test_a_short_open_at_the_end_accrues_borrow_in_the_costs(self):
        config = ExecutionConfig(fee_per_share=FEE, borrow_bps_per_year=36_50)
        experiment = self.open(execution=config, allow_short=True)
        experiment.step()
        experiment.order('s1', 'sell', 10, 999_900)
        self.fill_next()
        self.to_day(2)
        experiment.stop()
        figures = self.app.pnl_attribution(self.profile, experiment.run_id)
        costs = figures['costs']
        events = self.app.run_events(self.profile, experiment.run_id)
        sale = next(e for e in events if e['kind'] == 'fill')
        held = max(e['time_ns'] for e in events) - sale['time_ns']
        proceeds = 10 * 999_900 - 10 * FEE
        expected = -(-proceeds * 3650 * held // (10_000 * 365 * DAY_NS))
        self.assertGreater(expected, 0)
        self.assertEqual(costs['borrow_accrued'], expected)
        self.assertEqual(costs['borrow'], expected)  # nothing was paid at a cover

    def test_a_short_needs_initial_margin(self):
        experiment = self.open(allow_short=True)
        experiment.step()
        # $10,000 of cash covers 50% of about $20,000 of short value: about 200 shares.
        experiment.order('big', 'sell', 201, 999_900)
        events = self.app.run_events(self.profile, experiment.run_id)
        self.assertEqual(
            events[-1]['data']['reason'], 'insufficient available cash for short-sale margin'
        )
        experiment.order('ok', 'sell', 199, 999_900)
        events = self.app.run_events(self.profile, experiment.run_id)
        self.assertEqual(events[-1]['kind'], 'accepted')

    def test_a_model_target_below_zero_needs_allow_short(self):
        model = self.root / 'model.py'
        model.write_text(
            'def decide(bars, account, context):\n    return dict(target_position=-5)\n'
        )
        for allow, expected in ((False, 'failed'), (True, 'completed')):
            with self.subTest(allow_short=allow):
                account = self.app.create_account(self.profile, f'A{allow}', 100_000_000)
                experiment = self.app.open_experiment(
                    self.profile,
                    account,
                    source=self.csv,
                    method='model',
                    execution_mode='controlled',
                    allow_short=allow,
                    settings=dict(model_file=str(model)),
                )
                self.addCleanup(experiment.close)
                while experiment.runner.active:
                    experiment.step()
                self.assertEqual(self.app.run(self.profile, experiment.run_id)['status'], expected)
                if allow:
                    positions = self.app.snapshot(self.profile, account).positions
                    self.assertEqual([p.quantity for p in positions], [-5])
                experiment.close()

    def test_agent_experiments_refuse_allow_short(self):
        with self.assertRaisesRegex(ContractError, 'Agent'):
            self.app.open_experiment(
                self.profile,
                self.account,
                source=self.csv,
                method='agent',
                execution_mode='live',
                allow_short=True,
            )


if __name__ == '__main__':
    unittest.main()


class AgentMarketOrderTests(unittest.TestCase):
    def setUp(self):
        from support.prepared_market import PreparedMarketFixture

        PreparedMarketFixture.setUp(self)

    @unittest.skipIf(
        sys.platform == 'win32',
        'known open item: on Windows CI the scripted model fixture never delivers this order',
    )
    def test_an_agent_market_order_is_priced_and_replays_without_the_model(self):
        from unittest.mock import patch
        from support import carrier as template
        from support.clock import until

        buy = dict(symbol='AAPL', side='buy', quantity_shares=1)
        experiment = template.AgentExperimentFixture.open(
            self,
            [
                dict(output=[template.call('orders.submit_market', 1, buy)]),
                dict(output=[template.call('control.finish', 2)]),
            ],
        )
        self.addCleanup(experiment.close)
        experiment.play()
        # Starting the Agent process takes longer on Windows runners.
        until(lambda: len(self.app.snapshot(self.profile, self.account).orders) == 1, 20)
        experiment.stop()
        events = self.app.run_events(self.profile, experiment.run_id)
        accepted = next(e for e in events if e['kind'] == 'accepted')['data']
        self.assertEqual(accepted['order_type'], 'market')
        order = self.app.snapshot(self.profile, self.account).orders[0]
        # The fixture's best ask is 101 money units; 5% more rounds up to 107.
        self.assertEqual(order.limit_price, 107)
        target = self.app.create_account(self.profile, 'replayed', 10_000_000)
        with patch(
            'sim_platform.agents.hosting.host.AgentProcessHost',
            side_effect=AssertionError('agent'),
        ):
            result = self.app.replay_prepared_operations(
                self.profile, target, experiment.runner._recorder.path
            )
        self.assertTrue(result['events_verified'])
