"""Behavioral regressions for source reuse, cleanup diagnostics and runner contracts."""

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import struct
import unittest
from unittest.mock import Mock

from sim_platform import Platform, ContractError
from sim_platform.decisions.agent.runner import AgentDecisionRunner
from sim_platform.decisions.policy.live.runner import LivePolicyDecisionRunner
from sim_platform.desktop.workflows.experiments import ExperimentPreparation
from sim_platform.examples.market_demo import write_fixture
from support.agents import definition


class SourcePreparationTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source, self.news = write_fixture(self.root / 'input')
        self.app = Platform(self.root / 'workspace')
        self.addCleanup(self.app.close)
        self.controller = self.app.open_research(method='agent')
        self.start = 34_204_000_000_000
        self.settings = dict(
            symbol='AAPL', start_ns=str(self.start), source_manifest_ref='test-source'
        )

    def prepare(self, source=None, **options):
        self.controller.begin_prepare(
            source or self.source, start_mode='custom', start_time_ns=self.start, **options
        )
        self.controller.wait_for_preparation()

    def test_agent_reuses_prepared_cursor_and_keeps_news_and_start(self):
        self.prepare(news=self.news, allow_implicit_eof=True)
        market = self.controller.market
        self.controller._prepare_agent_source(self.source, self.settings)
        self.assertIs(self.controller.market, market)
        self.assertEqual(self.controller.source['news_path'], str(self.news.resolve()))
        self.assertTrue(self.controller.source['allow_implicit_eof'])
        self.assertEqual(self.controller.market.time_ns, self.start)
        self.assertEqual([n.record_id for n in self.controller.market.news()], ['earnings-demo'])

    def test_agent_keeps_valid_implicit_eof_recording(self):
        end = (
            b'S' + struct.pack('>HH', 0, 0) + (self.start + 4_000_000_000).to_bytes(6, 'big') + b'C'
        )
        self.source.write_bytes(self.source.read_bytes()[:-2] + struct.pack('>H', len(end)) + end)
        self.prepare(allow_implicit_eof=True)
        self.controller._prepare_agent_source(self.source, self.settings)
        self.assertEqual(self.controller.market.time_ns, self.start)
        self.assertTrue(self.controller.source['allow_implicit_eof'])

    def test_changed_start_preserves_source_options(self):
        self.prepare(news=self.news, allow_implicit_eof=True)
        settings = dict(self.settings, start_ns=str(self.start + 1_000_000_000))
        self.controller._prepare_agent_source(self.source, settings)
        self.assertEqual(self.controller.market.time_ns, self.start + 1_000_000_000)
        self.assertEqual(len(self.controller.market.news()), 1)
        self.assertTrue(self.controller.source['allow_implicit_eof'])

    def test_another_recording_does_not_inherit_selected_news(self):
        self.prepare(news=self.news, allow_implicit_eof=True)
        other, _ = write_fixture(self.root / 'other')
        self.controller._prepare_agent_source(other, self.settings)
        self.assertEqual(self.controller.source['path'], str(other.resolve()))
        self.assertIsNone(self.controller.source['news_path'])
        self.assertFalse(self.controller.source['allow_implicit_eof'])
        self.assertEqual(self.controller.market.news(), ())

    def test_modified_source_is_not_reused(self):
        self.prepare(news=self.news)
        market = self.controller.market
        # A changed/truncated source must be validated again, not reuse the old reader.
        self.source.write_bytes(self.source.read_bytes()[:-1])
        with self.assertRaises(ContractError):
            self.controller._prepare_agent_source(self.source, self.settings)
        self.assertIsNot(self.controller.market, market)

    def test_standalone_agent_preparation_has_the_requested_origin(self):
        self.controller._prepare_agent_source(self.source, self.settings)
        self.assertEqual(self.controller.market.time_ns, self.start)
        self.assertEqual(self.controller.source['experiment_start_ns'], self.start)

    def test_desktop_agent_creation_keeps_prepared_publications(self):
        profile = self.app.create_profile('Researcher')
        account = self.app.create_account(profile, 'Cash', 100_000_000)
        self.app.create_agent(profile, definition())
        experiment = ExperimentPreparation(self.app).prepare(
            arguments=dict(
                policy='agent',
                profile=profile,
                account=account,
                path=str(self.source),
                symbol='AAPL',
                news=str(self.news),
                allow_implicit_eof=True,
                start_mode='custom',
                start_time_ns=self.start,
                agent_id='demo',
                revision=1,
                end_ns=self.start + 2_000_000_000,
                fee_per_share=0,
                latency_ns=0,
                execution_mode='live',
            ),
            progress=lambda value: None,
            cancellation=SimpleNamespace(cancelled=False),
        )
        self.assertEqual(experiment.market.time_ns, self.start)
        self.assertEqual(experiment.source['news_path'], str(self.news.resolve()))
        self.assertTrue(experiment.source['allow_implicit_eof'])
        self.assertEqual([n.record_id for n in experiment.market.news()], ['earnings-demo'])
        self.assertEqual(experiment.runner.pipeline.snapshot()['state'], 'ready')


class PreparationFailureTests(unittest.TestCase):
    def test_original_failure_survives_cleanup_failure_with_diagnostics(self):
        original = ValueError('original preparation failure')
        controller = Mock()
        controller.begin_prepare.side_effect = original
        controller.close.side_effect = RuntimeError('cleanup failed')
        platform = Mock()
        platform.open_research.return_value = controller
        with self.assertRaises(ValueError) as caught:
            ExperimentPreparation(platform).prepare(
                arguments=dict(policy='agent', path='unused', symbol='AAPL'),
                progress=lambda value: None,
                cancellation=SimpleNamespace(cancelled=False),
            )
        self.assertIs(caught.exception, original)
        diagnostics = getattr(original, '__notes__', getattr(original, 'cleanup_errors', ()))
        self.assertTrue(any('cleanup failed' in message for message in diagnostics))
        controller.close.assert_called_once()


class BackgroundPollTests(unittest.TestCase):
    def test_background_runners_agree_on_the_poll_input_contract(self):
        agent = AgentDecisionRunner.__new__(AgentDecisionRunner)
        agent.snapshot = lambda: {'runner': 'agent'}
        policy = LivePolicyDecisionRunner.__new__(LivePolicyDecisionRunner)
        policy.detached_view = lambda: {'runner': 'policy'}
        invalid = (
            dict(max_events=0),
            dict(max_events=True),
            dict(budget_seconds=0),
            dict(budget_seconds=True),
            dict(budget_seconds=float('inf')),
            dict(budget_seconds=float('nan')),
        )
        for runner in (agent, policy):
            for arguments in invalid:
                with self.subTest(runner=type(runner).__name__, arguments=arguments):
                    with self.assertRaises(ContractError):
                        runner.poll(**arguments)
            self.assertIn('runner', runner.poll(max_events=2, budget_seconds=0.1))
            with self.assertRaises(TypeError):
                runner.poll(max_event=2)
