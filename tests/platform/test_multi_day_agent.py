"""An Agent experiment on a price-bar recording that covers two trading days.

The Agent, Tools and Runtime run as real child processes with a scripted model. Time is
the controlled test clock, built with the same closed-market skip the App would build.
"""

import json
import multiprocessing
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from sim_platform import Platform
from sim_platform.market.models import DAY_NS
from sim_platform.models.contracts import ModelProfile
from support.agents import definition, settings
from support.carrier import RecordingProvider, call
from support.clock import FakeTime, controlled_clock_class, until

SECOND = 1_000_000_000
OPEN_NS = (9 * 3600 + 30 * 60) * SECOND
SESSION_BARS = 5
# Day one trades near 100.00 and day two near 98.00, so a buy limit at 99.00 rests
# overnight and becomes marketable at the next open.
LIMIT_PRICE = '99.0000'


def two_sessions() -> str:
    rows = ['timestamp,open,high,low,close,volume']
    for day, price in (('2026-03-02', '100.00'), ('2026-03-03', '98.00')):
        for second in range(SESSION_BARS):
            rows.append(f'{day} 09:30:{second:02d},{price},{price},{price},{price},4000')
    return '\n'.join(rows) + '\n'


class MultiDayAgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix='multi-day-agent-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'two-days.csv'
        self.source.write_text(two_sessions())
        self.app = Platform(self.root / 'app')
        self.addCleanup(self.app.close)
        self.profile = self.app.create_profile('Agent')
        self.account = self.app.create_account(self.profile, 'Cash', 100_000_000)
        agent = definition()
        agent['schedule']['interval_seconds'] = '60'
        self.app.create_agent(self.profile, agent)

    def open(self, replies):
        """Open the Agent experiment with a controlled clock that has the App's skips."""
        self.real, clocks = FakeTime(), []

        grants = ['market.quote', 'orders.submit_limit', 'control.finish', 'model.request']
        config = dict(
            settings(),
            start_ns=str(OPEN_NS),
            end_ns=str(DAY_NS + OPEN_NS + 10 * SECOND),
            granted_tools=grants,
            timeout_seconds='5',
        )
        config['retry'].update(base_delay_seconds='0', max_delay_seconds='0')
        with patch(
            'sim_platform.experiments.scheduling.clock.SharedExperimentClock',
            controlled_clock_class(self.real, clocks),
        ):
            experiment = self.app.open_experiment(
                self.profile,
                self.account,
                source=self.source,
                method='agent',
                agent_id='demo',
                revision=1,
                settings=config,
                execution_mode='live',
            )
        self.addCleanup(experiment.close)
        self.clock = clocks[0]
        self.incoming, outgoing = multiprocessing.get_context('spawn').Pipe(duplex=False)
        self.addCleanup(self.incoming.close)
        self.app.set_model_credential('fixture', 'fixture-secret')
        try:
            self.app.configure_experiment_model(
                experiment,
                ModelProfile('openai-responses', 'mock', 'fixture'),
                provider=RecordingProvider(outgoing, replies),
            )
        finally:
            outgoing.close()
        return experiment

    def nudge(self):
        # A command's receipt time closes only once the clock moves past it.
        self.clock.advance_real(1)

    def starts(self, experiment):
        archive = self.app.agent_log_archive(self.profile, experiment.run_id)
        return [
            int(record['details']['dispatched_ns'])
            for record in archive['records']
            if record.get('event') == 'work_started'
        ]

    def test_agent_trades_across_the_closed_market_and_replay_verifies(self):
        buy = dict(symbol='AAPL', side='buy', quantity_shares=10, limit_price_usd=LIMIT_PRICE)
        experiment = self.open(
            [
                dict(output=[call('orders.submit_limit', 1, buy)]),
                dict(output=[call('control.finish', 2)]),
                dict(output=[call('market.quote', 3, dict(symbol='AAPL'))]),
                dict(output=[call('control.finish', 4)]),
            ]
        )
        self.assertEqual(len(experiment.runner.pipeline.reader.manifest['calendar']['gaps']), 1)
        experiment.play()
        idle = lambda: experiment.runner.snapshot()['work']['state'] == 'idle'  # noqa: E731
        replies = []
        for _ in range(2):
            until(self.incoming.poll, advance=self.nudge)
            replies.append(self.incoming.recv())
        until(idle, advance=self.nudge)
        orders = self.app.snapshot(self.profile, self.account).orders
        self.assertEqual([(o.status, o.filled) for o in orders], [('accepted', 0)])

        # Close of day one, through the grace period and the skip, to the next open.
        self.clock.advance_to(DAY_NS + OPEN_NS - 2 * SECOND - 1)
        until(self.incoming.poll, advance=self.nudge, timeout=5)
        replies.append(self.incoming.recv())
        until(self.incoming.poll, advance=self.nudge)
        replies.append(self.incoming.recv())
        until(idle, advance=self.nudge)
        # Grace period and lead only: the night cost under a minute of running time.
        self.assertLess(self.real.ns, 60 * SECOND)
        quote = json.loads(replies[-1]['payload']['input'][-1]['output'])
        self.assertEqual(quote['status'], 'ok', quote)

        self.clock.advance_to(DAY_NS + OPEN_NS + 10 * SECOND + 1)
        until(lambda: experiment.runner.pipeline.snapshot()['state'] == 'completed', timeout=5)
        until(lambda: experiment.runner.output_finished)

        # The schedule fired at the start and once after the skip: the ~1,440 intervals
        # that fell inside the closed market are not made up one by one.
        starts = self.starts(experiment)
        self.assertEqual(len(starts), 2, starts)
        self.assertLess(starts[0], OPEN_NS + SESSION_BARS * SECOND)
        self.assertGreaterEqual(starts[1], DAY_NS + OPEN_NS - 2 * SECOND)

        account = self.app.snapshot(self.profile, self.account)
        self.assertEqual([(o.status, o.filled) for o in account.orders], [('filled', 10)])
        events = self.app.run_events(self.profile, experiment.run_id)
        fill = next(e for e in events if e['kind'] == 'fill')
        self.assertEqual(fill['time_ns'] // DAY_NS, 1)
        self.assertLessEqual(fill['data']['price'], 990_000)

        target = self.app.create_account(self.profile, 'Replayed', 100_000_000)
        with (
            patch(
                'sim_platform.agents.hosting.host.AgentProcessHost',
                side_effect=AssertionError('agent'),
            ),
            patch(
                'sim_platform.models.providers.openai.OpenAIResponses.request',
                side_effect=AssertionError('model'),
            ),
        ):
            result = self.app.replay_prepared_operations(
                self.profile, target, experiment.runner._recorder.path
            )
        self.assertTrue(result['matched'])
        self.assertTrue(result['events_verified'])
        self.assertEqual([e['kind'] for e in result['events']], ['accepted', 'fill'])


if __name__ == '__main__':
    unittest.main()
