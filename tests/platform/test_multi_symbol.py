"""One experiment on several symbols (docs/architecture/multi-symbol-design.md)."""

import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))
from sim_platform import ContractError, Platform
from sim_platform.execution import ExecutionConfig
from sim_platform.market.models import DAY_NS

DAY0 = 34_200_000_000_000  # 09:30 on the first day
# AAPL starts a day before MSFT, so MSFT is re-based onto AAPL's first date.
AAPL = """date,open,high,low,close,volume
2026-01-05,100.00,100.00,100.00,100.00,100000
2026-01-06,90.00,90.00,90.00,90.00,100000
2026-01-07,95.00,95.00,95.00,95.00,100000
"""
MSFT = """date,open,high,low,close,volume
2026-01-06,50.00,50.00,50.00,50.00,100000
2026-01-07,55.00,55.00,55.00,55.00,100000
"""
FEE = 100


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix='multi-symbol-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'aapl.csv').write_text(AAPL)
        (self.root / 'msft.csv').write_text(MSFT)
        self.app = Platform(self.root / 'app')
        self.addCleanup(self.app.close)
        self.profile = self.app.create_profile('Quant')
        self.account = self.app.create_account(self.profile, 'Cash', 100_000_000)

    def open(self, symbols=('AAPL', 'MSFT'), files=('aapl.csv', 'msft.csv'), **settings):
        experiment = self.app.open_experiment(
            self.profile,
            self.account,
            source=[self.root / f for f in files],
            execution_mode='controlled',
            execution_config=ExecutionConfig(fee_per_share=FEE),
            settings=dict(symbols=list(symbols), start_mode='beginning', **settings),
        )
        self.addCleanup(experiment.close)
        self.experiment = experiment
        return experiment

    def step_until(self, predicate, limit=200):
        for _ in range(limit):
            if predicate(self.experiment.view()):
                return
            self.experiment.step()
        self.fail('condition not reached')

    def fills(self):
        events = self.app.run_events(self.profile, self.experiment.run_id)
        orders = {
            o.order_id: o.symbol for o in self.app.snapshot(self.profile, self.account).orders
        }
        return [
            (orders[e['data']['order_id']], e['data']['quantity'], e['data']['price'], e['time_ns'])
            for e in events
            if e['kind'] == 'fill'
        ]

    def replay(self):
        run_id = self.experiment.run_id
        while self.experiment.runner.active:
            self.experiment.step()
        self.experiment.close()
        self.app.close()
        return Platform.replay_run(self.root / 'app', run_id, self.root / 'replay')


class TimelineTests(Fixture):
    def test_recordings_with_different_first_dates_share_one_timeline(self):
        experiment = self.open()
        run = self.app.run(self.profile, experiment.run_id, include_records=False)
        self.assertEqual(run['universe'], ['AAPL', 'MSFT'])
        prepared = run['config']['research_source']['prepared']
        self.assertEqual(prepared['calendar']['start_date'], '2026-01-05')
        seen = []
        while experiment.runner.active:
            market = experiment.view()['market']
            seen.append((market['time_ns'], market['symbol']))
            experiment.step()
        first_msft = min(t for t, s in seen if s == 'MSFT')
        # MSFT's first bar (2026-01-06) lands on day 1, after AAPL's whole first day.
        self.assertEqual(first_msft // DAY_NS, 1)
        times = [t for t, _ in seen]
        self.assertEqual(times, sorted(times))

    def test_equal_times_follow_the_universe_order(self):
        for order in (('AAPL', 'MSFT'), ('MSFT', 'AAPL')):
            with self.subTest(order=order):
                account = self.app.create_account(self.profile, '-'.join(order), 100_000_000)
                self.account = account
                files = tuple(s.lower() + '.csv' for s in order)
                experiment = self.open(order, files)
                self.step_until(lambda view: view['market']['time_ns'] >= DAY_NS + DAY0)
                symbols = []
                while experiment.view()['market']['time_ns'] == DAY_NS + DAY0:
                    symbols.append(experiment.view()['market']['symbol'])
                    experiment.step()
                # Every event of the first symbol at this moment comes before the second's.
                self.assertEqual(symbols, sorted(symbols, key=order.index))
                self.assertEqual((symbols[0], symbols[-1]), order)
                experiment.close()

    def test_mismatched_sources_are_refused(self):
        # Rule and hold methods now run per symbol (test_multi_symbol_agent.PolicyTests).
        with self.assertRaisesRegex(ContractError, 'one source per symbol'):
            self.open(files=('aapl.csv', 'msft.csv', 'aapl.csv'))


class TradingTests(Fixture):
    def test_orders_on_each_symbol_fill_against_their_own_book_and_replay_verifies(self):
        experiment = self.open()
        self.step_until(lambda view: view['market']['time_ns'] >= DAY0)
        experiment.order('a1', 'buy', 10, 1_000_100, symbol='AAPL')
        # A buy limit that only MSFT's prices could reach never fills on AAPL's book.
        experiment.order('a2', 'buy', 5, 600_000, symbol='AAPL')
        self.step_until(lambda view: view['market']['symbol'] == 'MSFT')
        experiment.order('m1', 'buy', 20, order_type='market', symbol='MSFT')
        self.step_until(lambda view: len(self.fills()) == 2)
        fills = self.fills()
        self.assertEqual(
            [(s, q, p) for s, q, p, _ in fills], [('AAPL', 10, 1_000_100), ('MSFT', 20, 500_100)]
        )
        account = self.app.snapshot(self.profile, self.account)
        self.assertEqual(
            sorted((p.symbol, p.quantity) for p in account.positions), [('AAPL', 10), ('MSFT', 20)]
        )
        self.assertEqual(account.cash, 100_000_000 - 10 * 1_000_100 - 20 * 500_100 - 30 * FEE)
        orders = {o.intent_id.split(':intent:')[-1]: o.status for o in account.orders}
        self.assertEqual(orders['a2'], 'accepted')
        result = self.replay()
        self.assertTrue(result['verified'])
        self.assertEqual(result['fills'], 2)

    def test_result_statistics_pair_round_trips_per_symbol_and_agree_with_the_ledger(self):
        from sim_platform.desktop.presentation import performance

        experiment = self.open()
        self.step_until(lambda view: view['market']['time_ns'] >= DAY0)
        experiment.order('a-buy', 'buy', 10, 1_000_100, symbol='AAPL')
        self.step_until(lambda view: view['market']['symbol'] == 'MSFT')
        experiment.order('m-buy', 'buy', 20, 500_100, symbol='MSFT')
        self.step_until(lambda view: len(self.fills()) == 2)
        self.step_until(lambda view: view['market']['time_ns'] >= 2 * DAY_NS + DAY0)
        experiment.order('a-sell', 'sell', 10, 949_900, symbol='AAPL')
        experiment.order('m-sell', 'sell', 20, 549_900, symbol='MSFT')
        self.step_until(lambda view: len(self.fills()) == 4)
        while experiment.runner.active:
            experiment.step()
        run_id = experiment.run_id
        experiment.close()
        research = self.app.open_research()
        self.addCleanup(research.close)
        result = performance(research.view_run(self.profile, run_id))
        ledger = self.app.snapshot(self.profile, self.account)
        self.assertEqual(result['realized_pnl'], ledger.realized_pnl)
        self.assertEqual(result['round_trips'], 2)
        self.assertEqual(result['winning_trips'], 1)  # AAPL lost, MSFT gained
        # Every symbol's closes are saved: the whole account gets a Sharpe ratio and a
        # curve that ends at cash plus nothing held (both positions were sold).
        self.assertIsNotNone(result['sharpe_ratio'])
        self.assertEqual(result['series'][-1][1], ledger.cash)

    def test_an_order_for_a_symbol_outside_the_universe_is_rejected(self):
        experiment = self.open()
        self.step_until(lambda view: view['market']['time_ns'] >= DAY0)
        experiment.order('x', 'buy', 1, 1_000_100, symbol='TSLA')
        self.assertIn('outside', json.dumps(experiment.view()['decisions'][-1]))

    def test_a_model_sets_a_target_for_each_symbol_and_replays(self):
        model = self.root / 'model.py'
        model.write_text(
            'def decide(bars, account, context):\n'
            '    assert sorted(bars) == ["AAPL", "MSFT"] and context["symbols"] == ["AAPL", "MSFT"]\n'
            '    if account["positions"]["AAPL"] == 0 and bars["AAPL"]:\n'
            '        return {"targets": {"AAPL": 4, "MSFT": 6}}\n'
        )
        experiment = self.app.open_experiment(
            self.profile,
            self.account,
            source=[self.root / 'aapl.csv', self.root / 'msft.csv'],
            method='model',
            execution_mode='controlled',
            settings=dict(symbols=['AAPL', 'MSFT'], model_file=str(model)),
        )
        self.addCleanup(experiment.close)
        self.experiment = experiment
        while experiment.runner.active:
            experiment.step()
        self.assertEqual(self.app.run(self.profile, experiment.run_id)['status'], 'completed')
        positions = self.app.snapshot(self.profile, self.account).positions
        self.assertEqual(
            sorted((p.symbol, p.quantity) for p in positions), [('AAPL', 4), ('MSFT', 6)]
        )
        self.assertTrue(self.replay()['verified'])


class ItchUniverseTests(unittest.TestCase):
    def test_one_itch_file_prepared_for_two_symbols(self):
        from support.prepared_market import add, directory

        temp = TemporaryDirectory(prefix='multi-itch-')
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        start = DAY0
        source = root / 'two.bin'
        source.write_bytes(
            directory('AAPL', timestamp=start)
            + directory('MSFT', locate=8, timestamp=start + 1)
            + add(1, 'B', 100, 990_000, 'AAPL', timestamp=start + 2)
            + add(2, 'S', 100, 1_000_000, 'AAPL', timestamp=start + 3)
            + add(3, 'B', 100, 490_000, 'MSFT', locate=8, timestamp=start + 4)
            + add(4, 'S', 100, 500_000, 'MSFT', locate=8, timestamp=start + 5)
            + add(5, 'S', 10, 500_000, 'MSFT', locate=8, timestamp=start + 1_000)
            + add(6, 'S', 10, 1_000_000, 'AAPL', timestamp=start + 2_000)
            + b'\0\0'
        )
        app = Platform(root / 'app')
        self.addCleanup(app.close)
        profile = app.create_profile('Quant')
        account = app.create_account(profile, 'Cash', 100_000_000)
        experiment = app.open_experiment(
            profile,
            account,
            source=source,
            execution_mode='controlled',
            settings=dict(symbols=['AAPL', 'MSFT'], start_mode='first_book'),
        )
        self.addCleanup(experiment.close)
        experiment.order('m', 'buy', 5, 500_000, symbol='MSFT')
        experiment.order('a', 'buy', 5, 1_000_000, symbol='AAPL')
        while experiment.runner.active:
            experiment.step()
        positions = app.snapshot(profile, account).positions
        self.assertEqual(
            sorted((p.symbol, p.quantity) for p in positions), [('AAPL', 5), ('MSFT', 5)]
        )
        run_id = experiment.run_id
        experiment.close()
        app.close()
        self.assertTrue(Platform.replay_run(root / 'app', run_id, root / 'replay')['verified'])


if __name__ == '__main__':
    unittest.main()
