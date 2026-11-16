"""Result-page statistics: round trips, win rate, holding time, turnover and Sharpe."""

from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from sim_platform import Platform
from sim_platform.desktop.presentation import daily_sharpe, performance, round_trips
from sim_platform.execution import ExecutionConfig
from sim_platform.market.models import DAY_NS

DAILY = """date,open,high,low,close,volume
2026-01-05,100.00,101.00,99.00,100.50,10000
2026-01-06,101.00,102.00,100.00,101.50,10000
2026-01-07,99.00,100.00,98.00,98.50,10000
2026-01-08,103.00,104.00,102.00,103.50,10000
2026-01-09,104.00,105.00,103.00,104.50,10000
"""
# Buys 30 shares, sells 10 and then 20 (a partial sale inside one round trip), then
# a second round trip of 7 shares.
MODEL = '''
PLAN = {1: 30, 2: 20, 3: 0, 4: 7}
CALLS = []
def decide(bars, account, context):
    CALLS.append(1)
    target = PLAN.get(len(CALLS))
    return None if target is None else dict(target_position=target)
'''


def fill(time_ns, side, quantity, price, fee=0):
    return dict(time_ns=time_ns, side=side, quantity=quantity, price=price, fee=fee)


class RoundTripTests(unittest.TestCase):
    def test_partial_sales_stay_in_one_round_trip_and_use_floored_average_cost(self):
        rows = [
            fill(10, 'buy', 3, 100, fee=1),  # cost 301
            fill(20, 'sell', 1, 110, fee=1),  # removes 301 // 3 = 100; P&L 109 - 100 = 9
            fill(30, 'sell', 2, 90, fee=1),  # removes the remaining 201; P&L 179 - 201 = -22
            fill(40, 'buy', 1, 50),
            fill(55, 'sell', 1, 60),  # P&L 10
            fill(60, 'buy', 5, 10),  # still open at the end
            fill(70, 'sell', 2, 12),  # 4 realized inside the open trip
        ]
        result = round_trips(rows)
        self.assertEqual(
            result['trips'],
            [dict(open_ns=10, close_ns=30, pnl=-13), dict(open_ns=40, close_ns=55, pnl=10)],
        )
        self.assertEqual(result['realized_pnl'], 9 - 22 + 10 + 4)
        self.assertTrue(result['open'])

    def test_sharpe_uses_daily_closes_and_needs_two_returns(self):
        rows = [fill(5, 'buy', 10, 100)]
        # Starting cash 1000 all invested at 100; closes 110, 99 and 108.9.
        closes = [[0, 9, 110], [1, DAY_NS + 9, 99], [2, 2 * DAY_NS + 9, 1089 // 10]]
        sharpe = daily_sharpe(1000, rows, closes)
        from statistics import mean, stdev

        equity = [1000, 1100, 990, 1080]
        returns = [b / a - 1 for a, b in zip(equity, equity[1:])]
        self.assertAlmostEqual(sharpe, mean(returns) / stdev(returns) * 252**0.5)
        self.assertIsNone(daily_sharpe(1000, rows, closes[:1]))

    def test_inherited_holdings_give_no_statistics(self):
        view = dict(
            run=dict(initial_snapshot=dict(cash=1000, positions=[dict(symbol='A', quantity=1)])),
            fills=[],
        )
        result = performance(view)
        self.assertIsNone(result['win_rate'])
        self.assertIsNone(result['turnover'])


class SavedRunStatisticsTests(unittest.TestCase):
    def test_statistics_of_a_saved_run_agree_with_the_ledger(self):
        temp = TemporaryDirectory(prefix='result-statistics-')
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        (root / 'daily.csv').write_text(DAILY)
        (root / 'model.py').write_text(MODEL)
        app = Platform(root / 'app')
        self.addCleanup(app.close)
        profile = app.create_profile('Quant')
        account = app.create_account(profile, 'Cash', 100_000_000)
        experiment = app.open_experiment(
            profile,
            account,
            source=root / 'daily.csv',
            method='model',
            execution_mode='controlled',
            execution_config=ExecutionConfig(fee_per_share=100),
            settings=dict(model_file=str(root / 'model.py')),
        )
        self.addCleanup(experiment.close)
        while experiment.runner.active:
            experiment.step()
        run_id = experiment.run_id
        experiment.close()
        research = app.open_research()
        self.addCleanup(research.close)
        view = research.view_run(profile, run_id)
        result = performance(view)
        ledger = app.snapshot(profile, account)
        self.assertNotEqual(ledger.realized_pnl, 0)
        self.assertEqual(result['realized_pnl'], ledger.realized_pnl)
        self.assertEqual(result['realized_pnl'], view['summary']['run_realized_pnl'])
        # One closed round trip (30 shares, sold 10 then 20); the 7 shares stay open.
        self.assertEqual(result['round_trips'], 1)
        self.assertEqual(result['win_rate'], 1.0 if ledger.realized_pnl > 0 else 0.0)
        fills = [e for e in app.run_events(profile, run_id) if e['kind'] == 'fill']
        traded = sum(e['data']['quantity'] * e['data']['price'] for e in fills)
        self.assertAlmostEqual(result['turnover'], traded / 100_000_000)
        days = sorted({c[0] for c in view['daily_closes']})
        self.assertEqual(days, [0, 1, 2, 3, 4])
        self.assertIsNotNone(result['sharpe_ratio'])
        opening = min(e['time_ns'] for e in fills)
        # The trip closes with the sale on the third day (day 2).
        closing = max(e["time_ns"] for e in fills if e["time_ns"] < 3 * DAY_NS)
        self.assertEqual(result['average_holding_ns'], closing - opening)


if __name__ == '__main__':
    unittest.main()
