"""Execution cost models: latency jitter, square-root impact and queue position."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from itch_fixtures import add, reduce
from support.execution import ExecutionFixture
from sim_platform import ContractError, Intent
from sim_platform.execution import ExecutionConfig
from sim_platform.execution.costs import impacted_price, order_delay

QUEUE = 'displayed-limit-queue-v1'


class CostFunctionTests(unittest.TestCase):
    def test_jitter_is_bounded_and_fixed_by_order_id(self):
        config = ExecutionConfig(latency_ns=100, latency_jitter_ns=50)
        delays = [order_delay(config, f'run:order:{i}') for i in range(200)]
        self.assertTrue(all(100 <= d <= 150 for d in delays))
        self.assertGreater(len(set(delays)), 20)
        self.assertEqual(order_delay(config, 'run:order:7'), delays[7])
        self.assertEqual(order_delay(ExecutionConfig(latency_ns=100), 'x'), 100)

    def test_lognormal_latency_has_its_median_and_a_right_tail(self):
        config = ExecutionConfig(latency_ns=1_000_000, latency_sigma_milli=800)
        delays = sorted(order_delay(config, f'run:order:{i}') for i in range(2001))
        self.assertAlmostEqual(delays[1000] / 1_000_000, 1.0, delta=0.08)
        self.assertGreater(delays[-1] - delays[1000], delays[1000] - delays[0])
        self.assertTrue(all(0 <= d <= config.max_delay_ns for d in delays))
        with self.assertRaises(ContractError):
            ExecutionConfig(latency_ns=1, latency_sigma_milli=100, latency_jitter_ns=5)

    def test_square_root_impact_rounds_against_the_trader_and_respects_the_limit(self):
        config = ExecutionConfig(impact_bps=100)
        book = [(1_000_000, 400)]
        # A quarter of displayed depth: 100 bps * sqrt(0.25) = 50 bps.
        self.assertEqual(impacted_price(config, 'buy', 1_000_000, 100, book, 2_000_000), 1_005_000)
        self.assertEqual(impacted_price(config, 'sell', 1_000_000, 100, book, 0), 995_000)
        self.assertEqual(impacted_price(config, 'buy', 1_000_000, 100, book, 1_002_000), 1_002_000)
        self.assertEqual(impacted_price(config, 'sell', 1_000_000, 100, book, 999_000), 999_000)
        self.assertEqual(
            impacted_price(ExecutionConfig(), 'buy', 1_000_000, 100, book, 2_000_000), 1_000_000
        )

    def test_borrow_fee_is_charged_on_cover_for_the_time_short(self):
        from sim_platform.execution.costs import NS_PER_YEAR, borrow_fee, charge_borrow

        config = ExecutionConfig(borrow_bps_per_year=500)
        self.assertEqual(borrow_fee(config, 1_000_000, 0, NS_PER_YEAR), 50_000)
        self.assertEqual(borrow_fee(ExecutionConfig(), 1_000_000, 0, NS_PER_YEAR), 0)
        positions, shorts = {}, {}
        sides = {'s1': ('sell', 'X'), 's2': ('sell', 'X'), 'b1': ('buy', 'X')}
        charge_borrow(
            config,
            positions,
            sides,
            shorts,
            [dict(order_id='s1', quantity=10, price=100, fee=0)],
            0,
        )
        half = NS_PER_YEAR // 2
        charge_borrow(
            config,
            positions,
            sides,
            shorts,
            [dict(order_id='s2', quantity=10, price=100, fee=0)],
            half,
        )
        # Twenty shares, opened on average a quarter-year in.
        self.assertEqual(shorts, {'X': NS_PER_YEAR // 4})
        cover = dict(order_id='b1', quantity=20, price=90, fee=7)
        charge_borrow(config, positions, sides, shorts, [cover], NS_PER_YEAR)
        # 2,000 proceeds * 5% * 0.75 year = 75, added to the 7 fee.
        self.assertEqual((cover['fee'], cover['borrow_fee']), (82, 75))
        self.assertEqual(shorts, {})

    def test_invalid_settings_are_rejected(self):
        with self.assertRaises(ContractError):
            ExecutionConfig(model='made-up')
        with self.assertRaises(ContractError):
            ExecutionConfig(impact_bps=10_001)
        with self.assertRaises(ContractError):
            ExecutionConfig(latency_jitter_ns=-1)


class CostExecutionTests(unittest.TestCase, ExecutionFixture):
    setUp = ExecutionFixture.setUp

    def test_impact_raises_the_fill_price_and_records_the_quote(self):
        e, _, _, _ = self.scenario(config=ExecutionConfig(impact_bps=100))
        e.submit(Intent('big', 'AAPL', 'buy', 10, 1_020_000))
        e.step()
        (fill,) = e.last_fills
        # All 10 displayed shares: 100 bps on 1_001_000.
        self.assertEqual((fill['price'], fill['quoted_price']), (1_011_010, 1_001_000))
        self.assertEqual(e.snapshot.account.positions[0].cost, 10_110_100)

    def test_queue_model_fills_after_the_shares_ahead_trade(self):
        e, _, _, _ = self.scenario(
            tail=reduce('E', 1, 60, timestamp=4)
            + reduce('E', 1, 40, timestamp=5, match=124)
            + add(3, 'B', 50, 1_000_000, 'AAPL', timestamp=6)
            + reduce('E', 3, 15, timestamp=7, match=125),
            config=ExecutionConfig(model=QUEUE),
        )
        e.submit(Intent('rest', 'AAPL', 'buy', 20, 1_000_000))
        e.step()  # 60 of the 100 shares ahead trade
        e.step()  # the last 40 ahead trade: the order is now first
        e.step()  # a later order joins behind it
        self.assertEqual(e.snapshot.account.positions, ())
        e.step()  # the next 15 shares executed at the price are ours
        (fill,) = e.last_fills
        self.assertEqual(
            (fill['quantity'], fill['price'], fill['liquidity']), (15, 1_000_000, 'passive')
        )
        self.assertEqual(e.snapshot.account.orders[-1].status, 'partial')

    def test_cancellations_do_not_move_the_queue(self):
        e, _, _, _ = self.scenario(
            tail=reduce('X', 1, 90, timestamp=4) + reduce('E', 1, 10, timestamp=5, match=124),
            config=ExecutionConfig(model=QUEUE),
        )
        e.submit(Intent('rest', 'AAPL', 'buy', 20, 1_000_000))
        e.step()
        e.step()
        self.assertEqual(e.last_fills, ())
        self.assertEqual(e.snapshot.account.positions, ())

    def test_default_model_never_fills_resting_orders(self):
        e, _, _, _ = self.scenario(
            tail=reduce('E', 1, 60, timestamp=4) + reduce('E', 1, 40, timestamp=5, match=124)
        )
        e.submit(Intent('rest', 'AAPL', 'buy', 20, 1_000_000))
        e.step()
        e.step()
        self.assertEqual(e.snapshot.account.positions, ())


if __name__ == '__main__':
    unittest.main()
