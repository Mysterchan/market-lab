"""Per-experiment model budgets: tokens and USD, ending the run cleanly when spent."""

from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest

from sim_platform import Platform
from sim_platform.agents.configuration import AgentConfigurationError
from sim_platform.examples.configured_agent_demo import example_configuration
from sim_platform.examples.market_demo import write_fixture
from sim_platform.experiments.scheduling.policy.budget import ModelBudget
from sim_platform.models.contracts import ModelProfile
from sim_platform.models.providers.rehearsal import RehearsalProvider
from sim_platform.research.evaluation import agent_model_usage

GRANTS = ['market.quote', 'control.finish', 'model.request', 'orders.submit_market']


class BudgetTests(unittest.TestCase):
    def test_tokens_and_dollars_are_counted(self):
        budget = ModelBudget(
            dict(max_usd=0.01, prices=dict(input=2.0, output=10.0, cached_input=0.5))
        )
        self.assertIsNone(budget.exceeded())
        budget.add(dict(input_tokens=1000, output_tokens=200, cached_tokens=400))
        # 600 * 2 + 400 * 0.5 + 200 * 10 = 3400 USD per million tokens.
        self.assertAlmostEqual(budget.usd, 0.0034)
        self.assertEqual(budget.tokens, 1200)
        budget.add(dict(input_tokens=2000, output_tokens=400))
        spent = budget.exceeded()
        self.assertEqual(spent['limit'], 'max_usd')
        self.assertNotIn('prices', spent['budget'])
        tokens = ModelBudget(dict(max_tokens=10))
        tokens.add(dict(input_tokens=6, output_tokens=4))
        self.assertEqual(tokens.exceeded()['limit'], 'max_tokens')

    def test_settings_are_checked(self):
        from sim_platform.agents.configuration import _validate_budget

        for good in (dict(max_tokens=100), dict(max_usd=1, prices=dict(input=1, output=2))):
            _validate_budget(dict(budget=good))
        for bad in (
            {},
            dict(max_tokens=0),
            dict(max_usd=1),
            dict(max_usd=-1, prices=dict(input=1, output=1)),
            dict(max_usd=1, prices=dict(input=1)),
            dict(max_tokens=5, other=1),
        ):
            with self.assertRaises(AgentConfigurationError, msg=bad):
                _validate_budget(dict(budget=bad))


class BudgetRunTests(unittest.TestCase):
    def test_the_run_ends_when_its_token_budget_is_spent(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        source, _ = write_fixture(root)
        definition, settings = example_configuration()
        definition['requires'] = settings['granted_tools'] = GRANTS
        # Each rehearsal reply reports about 20 output tokens plus its input estimate.
        settings = dict(settings, speed='turn', budget=dict(max_tokens=30))
        app = Platform(root / 'accounts')
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
            settings=settings,
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
        experiment.tick(render=False)
        report = experiment.report()
        archive = app.agent_log_archive(profile, experiment.run_id)
        self.assertEqual(report['run']['status'], 'stopped')
        usage = agent_model_usage(archive)
        self.assertEqual(usage['model_calls'], 1)  # the second request was refused
        self.assertEqual(usage['budget_exceeded']['limit'], 'max_tokens')
        self.assertGreaterEqual(usage['budget_exceeded']['tokens'], 30)
        ended = [r for r in archive['records'] if r.get('event') == 'end']
        self.assertEqual(ended[-1]['details'].get('reason'), 'budget')


if __name__ == '__main__':
    unittest.main()
