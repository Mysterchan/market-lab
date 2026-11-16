"""Turn-based Agents: the market waits while the Agent works, then moves to its next turn."""

import multiprocessing
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest

from sim_platform import ContractError, Platform
from sim_platform.examples.configured_agent_demo import example_configuration
from sim_platform.examples.market_demo import write_fixture
from sim_platform.experiments.scheduling.clock import PACED_POLL_NS, SharedExperimentClock
from sim_platform.models.contracts import ModelProfile
from support.models import QuoteThenFinishProvider, SlowBuyThenFinishProvider

SECOND = 1_000_000_000
GRANTS = ['market.quote', 'control.finish', 'model.request', 'orders.submit_market']


class TurnClockTests(unittest.TestCase):
    def setUp(self):
        self.clock = SharedExperimentClock(multiprocessing.get_context('spawn'), 100, paced=True)

    def test_time_moves_only_when_advanced(self):
        self.clock.resume()
        time.sleep(0.01)
        self.assertEqual(self.clock.target_ns(), 100)
        self.assertTrue(self.clock.snapshot()['running'])
        self.clock.advance_to(500)
        self.assertEqual(self.clock.target_ns(), 500)
        self.clock.advance_to(300)  # never backwards
        self.assertEqual(self.clock.target_ns(), 500)
        self.assertEqual(self.clock.reader().target_ns(), 500)
        self.clock.pause()
        self.assertEqual(self.clock.target_ns(), 500)

    def test_waiters_are_told_to_look_again_soon(self):
        self.assertEqual(self.clock.delay_ns(100 + 10 * SECOND), PACED_POLL_NS)
        self.assertEqual(self.clock.delay_ns(50), 0)

    def test_a_live_clock_cannot_be_advanced(self):
        live = SharedExperimentClock(multiprocessing.get_context('spawn'), 0)
        with self.assertRaises(ContractError):
            live.advance_to(10)


class TurnBasedAgentTests(unittest.TestCase):
    def run_agent(self, speed, provider, **overrides):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        source, _ = write_fixture(root)
        definition, settings = example_configuration()
        definition['requires'] = settings['granted_tools'] = GRANTS
        definition['schedule'] = dict(definition['schedule'], **overrides.pop('schedule', {}))
        settings = dict(settings, speed=speed, **overrides)
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
            experiment, ModelProfile('openai-responses', 'fake', 'key'), provider=provider
        )
        started = time.monotonic()
        experiment.play()
        while experiment.runner.active:
            experiment.tick(render=False)
            time.sleep(0.01)
            self.assertLess(time.monotonic() - started, 30, 'experiment did not finish')
        # The Agent log is sealed and archived after the run ends.
        while not experiment.runner.archive_ready:
            experiment.tick(render=False)
            time.sleep(0.01)
            self.assertLess(time.monotonic() - started, 30, 'log was not archived')
        experiment.tick(render=False)
        snapshot = experiment.runner.snapshot()
        report = experiment.report()
        archive = app.agent_log_archive(profile, experiment.run_id)
        return report, snapshot, time.monotonic() - started, archive, int(settings['start_ns'])

    def accepted_ns(self, report):
        return next(e['time_ns'] for e in report['run_events'] if e['kind'] == 'accepted')

    def test_the_market_waits_while_the_model_answers(self):
        report, snapshot, _, _, start = self.run_agent('turn', SlowBuyThenFinishProvider(0.5))
        self.assertEqual(snapshot['pacing'], 'turn')
        self.assertEqual(report['run']['status'], 'completed')
        # Half a second of model time passed, but the order is stamped at the start.
        self.assertLess(self.accepted_ns(report) - start, 1000)
        self.assertEqual(report['summary']['fill_count'], 1)

        live, snapshot, _, _, start = self.run_agent('1x', SlowBuyThenFinishProvider(0.5))
        self.assertEqual(snapshot['pacing'], 'live')
        # In Live the market kept moving while the model answered.
        self.assertGreaterEqual(self.accepted_ns(live) - start, SECOND // 2)

    def test_every_scheduled_interval_is_a_turn_and_idle_time_is_skipped(self):
        report, snapshot, seconds, archive, start = self.run_agent(
            'turn', QuoteThenFinishProvider(), schedule=dict(interval_seconds='1')
        )
        self.assertEqual(report['run']['status'], 'completed')
        works = [r for r in archive['records'] if r.get('event') == 'work_started']
        # A four-second window with a one-second interval: turns at 0, 1, 2 and 3 seconds.
        self.assertEqual(len(works), 4)
        dispatched = [int(r['details']['dispatched_ns']) - start for r in works]
        self.assertEqual([round(t / SECOND) for t in dispatched], [0, 1, 2, 3])
        self.assertLess(seconds, 4)  # faster than the market window in real time
        self.assertIsNone(snapshot['pacer']['error'])

    def test_settings_accept_only_live_or_turn(self):
        with self.assertRaisesRegex(ContractError, "'1x' \\(Live\\) or 'turn'"):
            self.run_agent('2x', QuoteThenFinishProvider())


if __name__ == '__main__':
    unittest.main()
