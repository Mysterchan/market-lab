"""Decision attribution of Agent orders and PnL attribution of a run."""

import csv
import datetime
from pathlib import Path
import random
from tempfile import TemporaryDirectory
import time
import unittest

from sim_platform import Platform
from sim_platform.examples.configured_agent_demo import example_configuration
from sim_platform.examples.market_demo import write_fixture
from sim_platform.execution import ExecutionConfig
from sim_platform.models.contracts import ModelProfile
from sim_platform.models.providers.rehearsal import RehearsalProvider
from sim_platform.research.attribution import decision_timeline
from sim_platform.research.pnl_attribution import periods_per_year, regress

DAY_NS = 86_400 * 10**9
GRANTS = [
    'market.quote',
    'control.finish',
    'model.request',
    'orders.submit_market',
    'journal.write',
]


def write_bars(path, days=15):
    rng = random.Random(7)
    price = 100.0
    with path.open('w', newline='') as stream:
        out = csv.writer(stream)
        out.writerow(['Date', 'Open', 'High', 'Low', 'Close', 'Volume'])
        for i in range(days):
            day = datetime.date(2021, 3, 1) + datetime.timedelta(days=i)
            close = price * (1 + rng.gauss(0, 0.01))
            out.writerow(
                [day.isoformat(), price, max(price, close) + 1, min(price, close) - 1, close, 1e6]
            )
            price = close
    return path


class RegressionTests(unittest.TestCase):
    def test_known_alpha_and_beta_are_recovered(self):
        rng = random.Random(3)
        market = [rng.gauss(0, 0.01) for _ in range(200)]
        returns = [0.001 + 1.5 * m + rng.gauss(0, 0.0005) for m in market]
        fit = regress(returns, dict(market=market))
        self.assertAlmostEqual(fit['alpha'], 0.001, places=3)
        self.assertAlmostEqual(fit['betas']['market']['beta'], 1.5, places=1)
        self.assertGreater(fit['r_squared'], 0.95)
        self.assertGreater(fit['alpha_t'], 5)
        self.assertIn('reason', regress([0.1, 0.2], dict(market=[0.1, 0.2])))
        self.assertIn('reason', regress([0.01] * 10, dict(a=[1.0] * 10)))

    def test_periods_per_year_follow_bar_length(self):
        self.assertEqual(periods_per_year([0, DAY_NS, 2 * DAY_NS]), 252)
        minute = 60 * 10**9
        self.assertAlmostEqual(periods_per_year([0, minute, 2 * minute]), 252 * 390)


class PnLAttributionTests(unittest.TestCase):
    def test_a_rule_run_reports_beta_and_costs(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            source = write_bars(root / 'bars.csv')
            with Platform(root / 'app') as app:
                profile = app.create_profile('Research')
                account = app.create_account(profile, 'Cash', 1_000_000_000)
                experiment = app.open_experiment(
                    profile,
                    account,
                    source=source,
                    method='rule',
                    execution_mode='controlled',
                    settings=dict(quantity=500, hold_ns=8 * DAY_NS),
                    execution_config=ExecutionConfig(fee_per_share=10, impact_bps=5),
                )
                while experiment.runner.active:
                    experiment.step()
                run_id = experiment.run_id
                experiment.close()
                figures = app.pnl_attribution(profile, run_id)
                self.assertGreater(figures["periods"], 5)
                beta = figures['market']['betas']['market']['beta']
                # Holding about half the account in the stock gives a beta well above 0.
                self.assertGreater(beta, 0.1)
                self.assertLess(beta, 1.5)
                costs = figures['costs']
                self.assertEqual(costs['commission'], 2 * 500 * 10)
                self.assertGreater(costs['impact'], 0)
                self.assertEqual(figures['pnl']['gross'] - figures['pnl']['net'], costs['total'])
                extra = app.pnl_attribution(
                    profile, run_id, factors=dict(noise=[0.0] * figures['periods'])
                )
                self.assertIn('reason', extra['factors'])  # a constant factor is collinear


class DecisionTimelineTests(unittest.TestCase):
    def test_orders_link_to_the_reads_and_notes_of_their_round(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        source, _ = write_fixture(root)
        definition, settings = example_configuration()
        definition['requires'] = settings['granted_tools'] = GRANTS
        app = Platform(root / 'app')
        self.addCleanup(app.close)
        profile = app.create_profile('Research')
        app.create_agent(profile, definition)
        app.set_model_credential('key', 'unused')
        account = app.create_account(profile, 'Cash', 100_000_000)
        experiment = app.open_experiment(
            profile,
            account,
            source=source,
            method='agent',
            agent_id=definition['agent_id'],
            revision=1,
            settings=dict(settings, speed='turn'),
            execution_mode='live',
        )
        self.addCleanup(experiment.close)
        app.configure_experiment_model(
            experiment,
            ModelProfile('openai-responses', 'rehearsal', 'key'),
            provider=RehearsalProvider('AAPL'),
        )
        experiment.play()
        started = time.monotonic()
        while experiment.runner.active or not experiment.runner.archive_ready:
            experiment.tick(render=False)
            time.sleep(0.01)
            self.assertLess(time.monotonic() - started, 60)
        run_id = experiment.run_id
        experiment.close()
        timeline = app.decision_timeline(profile, run_id)
        self.assertGreaterEqual(len(timeline['rounds']), 1)
        order = timeline['orders'][0]
        self.assertEqual(order['tool'], 'orders.submit_market')
        self.assertEqual([read['tool'] for read in order['reads']], ['market.quote'])
        self.assertGreaterEqual(order['model_replies'], 1)
        self.assertEqual(order['notes_after'], ['Rehearsal decision recorded.'])
        self.assertEqual(len(order['fills']), 1)
        kinds = [step['kind'] for step in timeline['rounds'][0]['steps']]
        self.assertEqual(kinds[0], 'model')
        self.assertIn('journal', kinds)
        self.assertEqual(decision_timeline(None, []), dict(rounds=[], orders=[]))
        # The Desktop's saved result carries the same orders with their context.
        viewer = app.open_research()
        self.addCleanup(viewer.close)
        view = viewer.view_run(profile, run_id)
        self.assertEqual(view['agent_decisions'][0]['order_id'], order['order_id'])


if __name__ == '__main__':
    unittest.main()
