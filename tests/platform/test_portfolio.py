"""Portfolio construction helpers: weights, risk scaling, limits, shares and orders."""

import random
import unittest

from sim_platform.research import portfolio


def series(seed, volatility, count=400, beta=0.0, common=None):
    rng = random.Random(seed)
    return [beta * (common[i] if common else 0) + rng.gauss(0, volatility) for i in range(count)]


class PortfolioTests(unittest.TestCase):
    def setUp(self):
        self.returns = dict(LOW=series(1, 0.005), HIGH=series(2, 0.02))

    def test_lower_volatility_gets_more_weight(self):
        weights = portfolio.inverse_volatility(self.returns)
        self.assertAlmostEqual(sum(weights.values()), 1)
        self.assertGreater(weights['LOW'], 3 * weights['HIGH'])
        parity = portfolio.risk_parity(self.returns)
        # Uncorrelated assets: risk parity equals inverse volatility.
        self.assertAlmostEqual(parity['LOW'], weights['LOW'], places=2)
        minimum = portfolio.minimum_variance(self.returns)
        self.assertAlmostEqual(sum(minimum.values()), 1, places=6)
        self.assertGreater(minimum['LOW'], parity['LOW'])  # inverse variance, not volatility
        self.assertTrue(all(v >= 0 for v in minimum.values()))

    def test_volatility_target_and_limits(self):
        weights = portfolio.equal_weights(['LOW', 'HIGH'])
        scaled = portfolio.volatility_target(weights, self.returns, target=0.05, max_leverage=2)
        self.assertAlmostEqual(
            portfolio.portfolio_volatility(scaled, self.returns, 252), 0.05, places=6
        )
        capped = portfolio.apply_limits(
            dict(A=0.7, B=0.5, C=-0.1), max_weight=0.4, max_gross=0.6, max_net=0.3
        )
        self.assertLessEqual(max(abs(v) for v in capped.values()), 0.4)
        self.assertLessEqual(sum(abs(v) for v in capped.values()), 0.6 + 1e-12)
        self.assertLessEqual(sum(capped.values()), 0.3 + 1e-12)
        self.assertEqual(portfolio.signal_weights(dict(A=2, B=-2), gross=1), dict(A=0.5, B=-0.5))

    def test_shares_and_orders(self):
        shares = portfolio.target_shares(
            dict(A=0.5, B=-0.25), dict(A=1_000_000, B=500_000), 100_000_000, lot=10
        )
        self.assertEqual(shares, dict(A=50, B=-50))
        orders = portfolio.rebalance_orders(dict(A=20, C=5), shares, min_shares=2)
        self.assertEqual(
            orders,
            [
                dict(symbol='B', side='sell', quantity=50),
                dict(symbol='C', side='sell', quantity=5),
                dict(symbol='A', side='buy', quantity=30),
            ],
        )


if __name__ == '__main__':
    unittest.main()
