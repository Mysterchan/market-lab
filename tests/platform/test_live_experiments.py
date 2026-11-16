"""Live experiment integration across Platform, processes and native market."""

from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch
from sim_platform import Platform, ContractError
from support.clock import FakeTime, act_while_advancing, controlled_clock_class, until
from support.trading import fixture, ORIGIN, SECOND
from support.agents import definition, settings


def model_carrier(client, config):
    """Trusted real child capability proof; does not implement a model conversation loop."""
    work = client.next_work()
    result = client.request_model(work, dict(input='hello'))
    if result != dict(output=[]):
        raise RuntimeError('wrong child model result')
    client.wait_stopped()


def wait(predicate, timeout=3):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError('condition did not arrive')


class LiveExperiments(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = Platform(self.root / 'accounts')
        self.addCleanup(self.app.close)
        self.profile = self.app.create_profile('live')
        self.account = self.app.create_account(self.profile, 'cash', 100_000_000)
        self.source = fixture(self.root / 'source.bin')

    def test_human_market_progresses_without_poll_and_effective_order_time(self):
        clocks = []
        with patch(
            'sim_platform.application.experiments.factory.SharedExperimentClock',
            controlled_clock_class(FakeTime(), clocks),
        ):
            run = self.app.open_experiment(
                self.profile, self.account, source=self.source, execution_mode='live'
            )
        clock = clocks[0]
        run.play()
        # Nothing polls the view: market events are processed because the clock moved.
        clock.advance_to(ORIGIN + 3 * SECOND + SECOND // 2)
        until(lambda: run.runner.core.market_snapshot.time_ns == ORIGIN + 3 * SECOND)
        order = dict(client_id='h1', symbol='AAPL', side='buy', quantity=1, limit_price=1)
        result = act_while_advancing(
            clock, lambda: run.runner.act([dict(kind='submit', intent=order)])
        )
        self.assertIsNotNone(result['results'][0]['intent_id'])
        events = self.app.run_events(self.profile, run.run_id)
        intent = next(e for e in events if e['kind'] == 'intent')
        # The order takes effect when it was received, not at the last market event.
        self.assertGreaterEqual(intent['time_ns'], ORIGIN + 3 * SECOND + SECOND // 2)
        self.assertLess(intent['time_ns'], ORIGIN + 4 * SECOND)
        run.pause()
        control = run.runner.barrier.snapshot()
        self.assertEqual(control['state'], 'paused')
        acknowledgements = control['acknowledgements'][0]['acknowledgements']
        self.assertEqual({a['participant'] for a in acknowledgements}, {'execution', 'runtime'})
        self.assertTrue(all(a['phase'] == 'quiescent' for a in acknowledgements))
        self.assertEqual(run.runner.runtime.snapshot()['work']['state'], 'idle')
        target = run.runner.runtime.clock.target_ns()
        clock.advance_real(10 * SECOND)
        self.assertEqual(target, run.runner.runtime.clock.target_ns())
        run.play()
        run.stop()
        self.assertTrue(run.runner.closed)
        self.assertEqual(run.runner.barrier.snapshot()['state'], 'resources_closed')
        self.assertEqual(
            self.app.snapshot(self.profile, self.account).orders[0].status, 'cancelled'
        )

    def test_agent_work_uses_live_runtime(self):
        self.app.create_agent(self.profile, definition())
        config = dict(
            settings(), start_ns=str(ORIGIN + 2_000_000_000), end_ns=str(ORIGIN + 8_000_000_000)
        )
        run = self.app.open_experiment(
            self.profile,
            self.account,
            source=self.source,
            method='agent',
            agent_id='demo',
            revision=1,
            settings=config,
            execution_mode='live',
        )
        run.play()
        work = run.runner.next_work().result(2)['result']
        self.assertGreaterEqual(int(work['dispatched_ns']), int(work['scheduled_due_ns']))
        self.assertNotIn('position', work)
        run.stop()

    def test_rule_computes_in_child_and_owner_commits(self):
        run = self.app.open_experiment(
            self.profile, self.account, source=self.source, method='rule', execution_mode='live'
        )
        run.play()
        # Source's next event is two seconds after the first prepared book.
        wait(lambda: bool(run.runner.core.recent_decisions(100)), 4)
        record = run.runner.core.records[0]
        self.assertEqual(record['status'], 'ok', record)
        self.assertEqual(record['response']['actions'][0]['intent']['quantity'], 10)
        self.assertGreaterEqual(
            record['position']['sequence'], record['observation']['market']['sequence']
        )
        run.stop()


class LiveFailureMatrix(unittest.TestCase):
    setUp = LiveExperiments.setUp

    def agent(self):
        from sim_platform.agents.tools.protocol.contracts import IMPLEMENTED_TOOLS

        self.app.create_agent(self.profile, definition())
        config = dict(
            settings(),
            start_ns=str(ORIGIN + 2_000_000_000),
            end_ns=str(ORIGIN + 8_000_000_000),
            granted_tools=sorted(IMPLEMENTED_TOOLS),
        )
        result = self.app.open_experiment(
            self.profile,
            self.account,
            source=self.source,
            method='agent',
            agent_id='demo',
            revision=1,
            settings=config,
            execution_mode='live',
        )
        result.play()
        return result, result.runner.next_work().result(2)['result']

    def wire(self, run, work, tool, arguments, identity):
        return dict(
            run_id=run.run_id,
            work_id=work['work_id'],
            epoch=work['epoch'],
            request_id=identity,
            tool=tool,
            arguments=arguments,
        )

    def test_wait_order_version_baseline_and_partial_fill_stop(self):
        run, work = self.agent()
        buy = run.request_tool(
            self.wire(
                run,
                work,
                'orders.submit_limit',
                dict(symbol='AAPL', side='buy', quantity_shares=5, limit_price_usd='100.0000'),
                'buy',
            )
        ).result(2)['result']
        self.assertEqual(buy['status'], 'ok')
        order = buy['data']['order']
        waiting = run.request_tool(
            self.wire(
                run,
                work,
                'control.wait',
                dict(
                    conditions=[
                        dict(
                            kind='order_changed',
                            order_id=order['order_id'],
                            after_version=order['version'],
                        )
                    ]
                ),
                'wait',
            )
        ).result(2)['result']
        self.assertEqual(waiting['status'], 'ok', waiting)
        next_work = run.runner.next_work().result(3)['result']
        self.assertEqual(next_work['work_id'], 'work-2')
        replay = run.request_tool(
            self.wire(
                run,
                work,
                'orders.submit_limit',
                dict(symbol='AAPL', side='buy', quantity_shares=5, limit_price_usd='100.0000'),
                'buy',
            )
        ).result(2)['result']
        self.assertEqual(replay, buy)
        run.stop()
        actual = self.app.snapshot(self.profile, self.account).orders[0]
        self.assertEqual((actual.filled, actual.status), (2, 'cancelled'))

    def test_runtime_crash_is_platform_fault_and_reaped(self):
        run, work = self.agent()
        run.request_tool(
            self.wire(
                run,
                work,
                'orders.submit_limit',
                dict(symbol='AAPL', side='buy', quantity_shares=1, limit_price_usd='0.0001'),
                'buy',
            )
        ).result(2)
        runtime = run.runner._tools.controls.runtime
        runtime._process.terminate()
        wait(lambda: not run.runner.active)
        self.assertEqual(run.runner.pipeline.snapshot()['state'], 'failed')
        run.close()
        self.assertFalse(runtime.snapshot()['alive'])
        self.assertEqual(
            self.app.snapshot(self.profile, self.account).orders[0].status, 'cancelled'
        )

    def test_agent_crash_preserves_accepted_order_and_market(self):
        run, work = self.agent()
        run.request_tool(
            self.wire(
                run,
                work,
                'orders.submit_limit',
                dict(symbol='AAPL', side='buy', quantity_shares=1, limit_price_usd='0.0001'),
                'buy',
            )
        ).result(2)
        run.runner._process._process.terminate()
        wait(lambda: run.runner._tools.controls.snapshot()['state'] == 'failed')
        self.assertTrue(run.runner.active)
        self.assertEqual(self.app.snapshot(self.profile, self.account).orders[0].status, 'accepted')
        run.close()

    def test_tools_crash_after_commit_preserves_original_financial_result(self):
        run, work = self.agent()
        request = self.wire(
            run,
            work,
            'orders.submit_limit',
            dict(symbol='AAPL', side='buy', quantity_shares=1, limit_price_usd='0.0001'),
            'buy',
        )
        original = run.request_tool(request).result(2)['result']
        worker = run.runner._tools._worker
        worker._process.terminate()
        wait(lambda: worker.snapshot()['error'] is not None)
        repeated = run.request_tool(request).result(2)['result']
        self.assertEqual(repeated, original)
        self.assertEqual(len(self.app.snapshot(self.profile, self.account).orders), 1)
        run.stop()

    def test_model_configuration_native_runtime_secret_not_exported(self):
        from sim_platform.models.contracts import ModelProfile
        from support.models import MockProvider

        self.app.set_model_credential('model-test', 'synthetic-private-value')
        # Configure before Play, using the same sole experiment root.
        self.app.create_agent(self.profile, definition())
        config = dict(
            settings(), start_ns=str(ORIGIN + 2_000_000_000), end_ns=str(ORIGIN + 8_000_000_000)
        )
        from functools import partial
        from unittest.mock import patch
        from sim_platform.agents.hosting.host import AgentProcessHost

        with patch(
            'sim_platform.agents.hosting.host.AgentProcessHost',
            partial(AgentProcessHost, program=model_carrier),
        ):
            run = self.app.open_experiment(
                self.profile,
                self.account,
                source=self.source,
                method='agent',
                agent_id='demo',
                revision=1,
                settings=config,
                execution_mode='live',
            )
        self.app.configure_experiment_model(
            run,
            ModelProfile('openai-responses', 'mock', 'model-test'),
            provider=MockProvider([dict(output=[])]),
        )
        run.play()
        wait(lambda: run.runner._tools.controls.snapshot()['attempt']['state'] == 'completed')
        run.stop()
        log = run.runner._recorder.path.read_text()
        self.assertIn('model_profile', log)
        self.assertNotIn('synthetic-private-value', log)
        self.assertNotIn('synthetic-private-value', str(self.app.run(self.profile, run.run_id)))

    def test_controlled_replay_explicitly_rejects_live_run(self):
        from sim_platform.persistence.replay.storage import read_inputs

        # Existing replay guard is verified without starting model/runtime work.
        run = self.app.open_experiment(
            self.profile, self.account, source=self.source, execution_mode='live'
        )
        self.assertEqual(
            self.app.run(self.profile, run.run_id)['config']['research_source']['execution_mode'],
            'live',
        )
        run.stop()
        with self.assertRaisesRegex(ContractError, 'operation Replay'):
            read_inputs(
                self.app.directory, run.run_id, recover=False, snapshot_reader=lambda *args: None
            )

    def test_real_agent_child_requests_model_without_credentials(self):
        from functools import partial
        from unittest.mock import patch
        from sim_platform.agents.hosting.host import AgentProcessHost
        from sim_platform.models.contracts import ModelProfile
        from support.models import MockProvider

        self.app.create_agent(self.profile, definition())
        config = dict(
            settings(), start_ns=str(ORIGIN + 2_000_000_000), end_ns=str(ORIGIN + 8_000_000_000)
        )
        with patch(
            'sim_platform.agents.hosting.host.AgentProcessHost',
            partial(AgentProcessHost, program=model_carrier),
        ):
            run = self.app.open_experiment(
                self.profile,
                self.account,
                source=self.source,
                method='agent',
                agent_id='demo',
                revision=1,
                settings=config,
                execution_mode='live',
            )
        provider = MockProvider([dict(output=[])])
        self.app.set_model_credential('model-test', 'synthetic-private-value')
        self.app.configure_experiment_model(
            run, ModelProfile('openai-responses', 'mock', 'model-test'), provider=provider
        )
        run.play()
        self.assertEqual(provider.calls, 0)  # App never invokes the provider.
        wait(lambda: run.runner._tools.controls.snapshot()['attempt']['state'] == 'completed')
        self.assertEqual(run.runner._process.snapshot()['state'], 'running')
        run.stop()
        self.assertNotIn('synthetic-private-value', run.runner._recorder.path.read_text())


class RuntimeProtocolMatrix(unittest.TestCase):
    def test_runtime_binding_recovers_retained_gap(self):
        from sim_platform.application.experiments.binding import RuntimeBinding
        from support.scheduling import configuration
        from sim_platform.experiments.scheduling.policy.controller import LiveWorkPolicy
        from sim_platform.experiments.scheduling.host import ExperimentRuntimeHandle

        class WakeOnly:
            def wake(self):
                pass

            def publish_notice(self, event, details):
                pass

        host = ExperimentRuntimeHandle(LiveWorkPolicy, configuration())
        try:
            binding = RuntimeBinding(host, WakeOnly(), {}, None)
            first = binding._journal.append(dict(sequence=1, orders={}), [])
            second = binding._journal.append(dict(sequence=2, orders={}), [])
            binding._submit('observe', second)
            wait(lambda: host.snapshot().get('event_sequence') == 2)
            self.assertIsNone(binding._error)
            self.assertEqual(host.submit('observe', first).result(2), dict(duplicate=True))
        finally:
            host.close()

    def test_bounded_event_journal_gap_no_fabricated_latest_baseline(self):
        from sim_platform.experiments.scheduling.journal import EventJournal

        journal = EventJournal(2)
        for index in range(3):
            journal.append(
                dict(sequence=index, orders={'order': index}),
                [dict(kind='order_update', order_id='order')],
            )
        self.assertEqual([v['event_sequence'] for v in journal.after(1)], [2, 3])
        with self.assertRaises(ContractError):
            journal.after(0)

    def test_control_reserved_capacity_duplicate_version_and_stale(self):
        from support.scheduling import configuration
        from sim_platform.experiments.scheduling.policy.controller import LiveWorkPolicy
        from sim_platform.experiments.scheduling.host import ExperimentRuntimeHandle

        host = ExperimentRuntimeHandle(LiveWorkPolicy, configuration(), capacity=1)
        try:
            pending = host.submit('next_work')
            control = dict(control_id='1', control_version=1, action='pause')
            first = host.submit('control', control).result(2)
            self.assertEqual(host.submit('control', control).result(2), first)
            with self.assertRaises(ContractError):
                host.submit('control', dict(control, action='resume')).result(2)
            with self.assertRaises(ContractError):
                host.submit('control', dict(control, control_version=3)).result(2)
            self.assertFalse(pending.done())
        finally:
            host.close()
