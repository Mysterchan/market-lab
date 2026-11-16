"""What the Desktop needs for an experiment on several symbols: per-symbol view, archive,
account valuation, Sharpe ratio and equity curve (docs/user-guide/several-symbols.md)."""

from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from sim_platform import ContractError, Platform
from sim_platform.desktop.presentation import (
    performance,
    portfolio_daily_sharpe,
    portfolio_series,
    portfolio_values,
)

AAPL = """date,open,high,low,close,volume
2026-01-05,100.00,100.00,100.00,100.00,100000
2026-01-06,90.00,90.00,90.00,90.00,100000
2026-01-07,95.00,95.00,95.00,95.00,100000
"""
MSFT = """date,open,high,low,close,volume
2026-01-06,50.00,50.00,50.00,50.00,100000
2026-01-07,55.00,55.00,55.00,55.00,100000
"""


class _Cancel:
    cancelled = False


class DesktopPreparationTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory(prefix='multi-desktop-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / 'aapl.csv').write_text(AAPL)
        (self.root / 'msft.csv').write_text(MSFT)
        self.app = Platform(self.root / 'app')
        self.addCleanup(self.app.close)
        self.profile = self.app.create_profile('Quant')
        self.account = self.app.create_account(self.profile, 'Pair', 100_000_000)

    def prepare(self):
        from sim_platform.desktop.workflows.experiments import ExperimentPreparation

        paths = [str(self.root / 'aapl.csv'), str(self.root / 'msft.csv')]
        controller = ExperimentPreparation(self.app).prepare(
            arguments=dict(
                policy='human',
                path=paths[0],
                symbol='AAPL',
                symbols=['AAPL', 'MSFT'],
                paths=paths,
                profile=self.profile,
                account=self.account,
                fee_per_share=100,
                latency_ns=0,
                quantity=10,
                hold_ns=1,
                execution_mode='controlled',
                start_mode='beginning',
            ),
            progress=lambda _: None,
            cancellation=_Cancel(),
        )
        self.addCleanup(controller.close)
        return controller

    def test_the_desktop_prepares_both_symbols_and_shows_each_book(self):
        controller = self.prepare()
        while 'MSFT' not in {
            s for s, m in controller.view()['markets'].items() if m['market']['bids']
        }:
            controller.step()
        view = controller.view()
        self.assertEqual(view['run']['universe'], ['AAPL', 'MSFT'])
        self.assertEqual(list(view['markets']), ['AAPL', 'MSFT'])
        aapl, msft = (view['markets'][s]['market'] for s in ('AAPL', 'MSFT'))
        self.assertEqual((aapl['symbol'], msft['symbol']), ('AAPL', 'MSFT'))
        self.assertLess(msft['bids'][0][0], 600_000)
        self.assertGreater(aapl['bids'][0][0], 800_000)
        self.assertTrue(view['markets']['AAPL']['bars'])

    def test_a_finished_run_saves_every_symbols_closes_and_values_the_account(self):
        controller = self.prepare()

        def quoted(symbol):
            return bool(controller.view()['markets'][symbol]['market']['bids'])

        while not quoted('AAPL'):
            controller.step()
        controller.order('a', 'buy', 10, 1_000_100, symbol='AAPL')
        while not quoted('MSFT'):
            controller.step()
        controller.order('m', 'buy', 20, 500_100, symbol='MSFT')
        while controller.runner.active:
            controller.step()
        research = self.app.open_research()
        self.addCleanup(research.close)
        view = research.view_run(self.profile, controller.run_id)
        closes = {s: m['daily_closes'] for s, m in view['markets'].items()}
        self.assertEqual([c[2] for c in closes['AAPL']], [1_000_000, 900_000, 950_000])
        self.assertEqual([c[2] for c in closes['MSFT']], [500_000, 550_000])
        held = {p['symbol']: p['quantity'] for p in view['account']['positions']}
        self.assertEqual(held, {'AAPL': 10, 'MSFT': 20})
        result = performance(view)
        # Ending equity: cash plus each holding at its own last close.
        self.assertEqual(
            result['series'][-1][1], view['account']['cash'] + 10 * 950_000 + 20 * 550_000
        )
        self.assertEqual(result['sharpe_returns'], 3)
        self.assertIsNotNone(result['sharpe_ratio'])

    def test_only_manual_can_take_over_an_experiment_on_several_symbols(self):
        controller = self.prepare()
        for policy in ('rule', 'hold'):
            with self.assertRaisesRegex(ContractError, 'only switch to Manual'):
                controller.handoff(policy)


class ValuationTests(unittest.TestCase):
    def test_each_position_is_marked_against_its_own_symbols_book(self):
        view = dict(
            account=dict(
                cash=1_000,
                positions=[dict(symbol='AAPL', quantity=2), dict(symbol='MSFT', quantity=-3)],
            ),
            market=dict(symbol='AAPL', bids=[[100, 1]], asks=[[101, 1]]),
            markets=dict(
                AAPL=dict(market=dict(symbol='AAPL', bids=[[100, 1]], asks=[[101, 1]])),
                MSFT=dict(market=dict(symbol='MSFT', bids=[[50, 1]], asks=[[51, 1]])),
            ),
            run=dict(initial_snapshot=dict(cash=900, positions=[])),
        )
        # Long AAPL at its bid, short MSFT at its ask.
        self.assertEqual(portfolio_values(view), (1_000 + 2 * 100 - 3 * 51, 147, 1_000))
        view['markets']['MSFT']['market']['asks'] = []
        self.assertEqual(portfolio_values(view)[0], None)

    def test_daily_sharpe_carries_a_symbols_last_close_forward(self):
        rows = [
            dict(time_ns=1, side='buy', symbol='A', quantity=1, price=100, fee=0),
            dict(time_ns=2, side='buy', symbol='B', quantity=2, price=50, fee=0),
        ]
        # Day 1 has no close for B: B keeps 50.
        closes = dict(A=[[0, 10, 110], [1, 20, 120], [2, 30, 100]], B=[[0, 10, 50], [2, 30, 60]])
        days, sharpe = portfolio_daily_sharpe(1_000, rows, closes)
        equities = [800 + 110 + 100, 800 + 120 + 100, 800 + 100 + 120]
        returns = [equities[0] / 1_000 - 1] + [b / a - 1 for a, b in zip(equities, equities[1:])]
        from statistics import mean, stdev

        self.assertEqual(days, 3)
        self.assertAlmostEqual(sharpe, mean(returns) / stdev(returns) * 252**0.5)

    def test_equity_curve_marks_every_holding_at_its_latest_close(self):
        bar = lambda start, close, o=None: dict(  # noqa: E731
            start_ns=start, end_ns=start + 10, open=o or close, close=close, complete=True
        )
        bars = dict(A=[bar(0, 100), bar(10, 110)], B=[bar(0, 50), bar(20, 40)])
        rows = [
            dict(time_ns=5, side='buy', symbol='A', quantity=1, price=100, fee=1),
            dict(time_ns=15, side='buy', symbol='B', quantity=2, price=50, fee=1),
        ]
        series = portfolio_series(1_000, rows, bars)
        self.assertEqual(
            series,
            [
                [0, 1_000],
                [10, 899 + 100],
                [20, 798 + 110 + 2 * 50],
                [30, 798 + 110 + 2 * 40],
            ],
        )
        self.assertIsNone(portfolio_series(1_000, rows, dict(A=bars['A'], B=[])))


if __name__ == '__main__':
    unittest.main()
