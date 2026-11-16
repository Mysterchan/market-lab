"""Unified experiment entry, runner-owned pacing and explicit control capabilities."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import inspect
from unittest.mock import patch

from sim_platform import Platform, ContractError
from sim_platform.decisions import DecisionRunner, PolicyDecisionRunner
from sim_platform.examples.market_demo import write_fixture
from sim_platform.research.controller import ResearchController
from sim_platform.research.policy_controller import PolicyResearchController
from sim_platform.research.agent_controller import AgentResearchController


class ExperimentControlsTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = Platform(self.root / 'accounts')
        self.addCleanup(self.app.close)
        self.profile = self.app.create_profile('Researcher')
        self.account = self.app.create_account(self.profile, 'Cash', 100_000_000)
        self.source, self.news = write_fixture(self.root / 'fixture')

    def open(self, method='human', **options):
        return self.app.open_experiment(
            self.profile,
            self.account,
            source=self.source,
            method=method,
            execution_mode='controlled',
            **options,
        )

    def test_human_experiment_uses_research_composition_and_explicit_controls(self):
        with patch.object(self.app, 'open_research', wraps=self.app.open_research) as compose:
            experiment = self.open()
        compose.assert_called_once()
        self.assertIsInstance(experiment.runner, DecisionRunner)
        self.assertIsInstance(experiment.runner, PolicyDecisionRunner)
        self.assertIsInstance(experiment, PolicyResearchController)
        self.assertEqual(len(self.app._resources._roots), 1)
        self.assertEqual(len(self.app.runs(self.profile)), 1)
        experiment.policy_controls.order('passive', 'buy', 1, 1)
        experiment.policy_controls.cancel('passive')
        with self.assertRaises(ContractError):
            experiment.tool_controls
        experiment.stop()
        self.assertEqual(experiment.report()['final']['orders'][0]['status'], 'cancelled')

    def test_execution_mode_is_required_and_research_has_no_mode_default(self):
        self.assertNotIn('backend', inspect.signature(self.app.open_research).parameters)
        self.assertNotIn('execution_mode', inspect.signature(self.app.open_research).parameters)
        research = self.app.open_research()
        self.assertIsNone(research.execution_mode)
        with self.assertRaises(TypeError):
            self.app.open_experiment(self.profile, self.account, source=self.source)
        with self.assertRaises(TypeError):
            research.create_experiment(self.profile, self.account, source=self.source)
        self.assertEqual(self.app.runs(self.profile), [])

    def test_both_preparation_entries_select_the_same_execution_mode(self):
        from sim_platform.decisions.policy.live.runner import LivePolicyDecisionRunner

        for mode, expected in [
            ('controlled', PolicyDecisionRunner),
            ('live', LivePolicyDecisionRunner),
        ]:
            with self.subTest(mode=mode):
                account = self.app.create_account(self.profile, mode, 100_000_000)
                one_shot = self.app.open_experiment(
                    self.profile, account, source=self.source, execution_mode=mode
                )
                staged = self.app.open_research()
                staged.create_experiment(
                    self.profile, self.account, source=self.source, execution_mode=mode
                )
                for experiment in (one_shot, staged):
                    self.assertIsInstance(experiment.runner, expected)
                    self.assertEqual(experiment.execution_mode, mode)
                    self.assertEqual(experiment.view()['execution_mode'], mode)
                    config = self.app.run(self.profile, experiment.run_id)['config']
                    self.assertEqual(config['research_source']['execution_mode'], mode)
                    self.assertNotIn('runtime_backend', config['research_source'])
                    experiment.close()

    def test_unknown_and_historical_mode_labels_fail_before_creating_resources(self):
        roots = len(self.app._resources._roots)
        for mode in ['legacy', 'independent', 'LIVE', None, True]:
            with self.subTest(mode=mode), self.assertRaises(ContractError):
                self.app.open_experiment(
                    self.profile, self.account, source=self.source, execution_mode=mode
                )
        self.assertEqual(len(self.app._resources._roots), roots)
        self.assertEqual(self.app.runs(self.profile), [])

    def test_platform_selects_two_subclasses_of_abstract_controller(self):
        self.assertTrue(inspect.isabstract(ResearchController))
        policy = self.app.open_research()
        agent = self.app.open_research(method='agent')
        self.assertIsInstance(policy, ResearchController)
        self.assertIsInstance(agent, ResearchController)
        self.assertIsInstance(agent, AgentResearchController)
        self.assertFalse(hasattr(policy, 'start_agent'))
        self.assertFalse(hasattr(agent, 'order'))
        self.assertFalse(hasattr(agent, 'handoff'))
        self.assertEqual(len(self.app._resources._roots), 2)

    def test_runner_paces_without_controller_ticks_and_resume_rebases_clock(self):
        experiment = self.open()
        runner = experiment.runner
        origin = experiment.market.time_ns
        # Next trade is +2s away from the prepared book. Only runner.poll is used.
        with patch('sim_platform.market.pacing.monotonic', return_value=10):
            runner.play(1)
            runner.poll(max_events=100)
        self.assertEqual(experiment.market.time_ns, origin)
        with patch('sim_platform.market.pacing.monotonic', return_value=11):
            runner.poll(max_events=100)
        self.assertEqual(experiment.market.time_ns, origin)
        with patch('sim_platform.market.pacing.monotonic', return_value=12):
            runner.poll(max_events=100)
        self.assertEqual(experiment.market.time_ns, origin + 2_000_000_000)
        runner.pause()
        with patch('sim_platform.market.pacing.monotonic', return_value=100):
            runner.play(1)
            runner.poll(max_events=100)
        self.assertEqual(experiment.market.time_ns, origin + 2_000_000_000)

    def test_rule_entry_preserves_controlled_decisions_fills_and_fees(self):
        controlled_account = self.app.create_account(self.profile, 'Controlled', 100_000_000)
        controlled = self.app.open_research()
        controlled.load(self.source, news=self.news)
        controlled.start(
            self.profile, controlled_account, policy='rule', execution_mode='controlled'
        )
        experiment = self.open('rule', settings=dict(start_mode='beginning', news=str(self.news)))
        for controller in (controlled, experiment):
            controller.play()
            while controller.active:
                controller.tick(max_events=100, budget_seconds=1)
        a, b = controlled.report(), experiment.report()
        for field in ('cash', 'fees', 'realized_pnl', 'positions'):
            self.assertEqual(a['final'][field], b['final'][field])
        self.assertEqual(
            [d['response'] for d in a['decisions']], [d['response'] for d in b['decisions']]
        )

    def test_invalid_method_or_foreign_options_leave_no_run_or_root(self):
        for options in (
            dict(method='unknown'),
            dict(agent_id='wrong'),
            dict(settings=dict(unknown=True)),
            dict(method='agent'),
        ):
            with self.assertRaises(ContractError):
                self.open(**options)
            self.assertEqual(self.app.runs(self.profile), [])
            self.assertEqual(self.app._resources._roots, {})

    def test_invalid_speed_rejects_before_starting_market(self):
        experiment = self.open()
        before = experiment.market.status
        for speed in (True, 0, -1, float('inf')):
            with self.assertRaises(ContractError):
                experiment.play(speed)
        self.assertEqual(experiment.market.status, before)


if __name__ == '__main__':
    unittest.main()
