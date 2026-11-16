"""Agent spawn/IPC integration, backpressure and independent market ownership."""

from concurrent.futures import Future
import multiprocessing
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import sys
from threading import enumerate as threads
import time
import unittest
from unittest.mock import patch

from sim_platform import Platform, ContractError, Intent
from sim_platform.agents.hosting.protocol import TransportLimits, packet, unpack
from sim_platform.agents.hosting.host import AgentProcessHost
from support.clock import TestClock as ExperimentClock
from sim_platform.examples.market_demo import write_fixture
from support.agents import definition, settings
from support.clock import FakeTime, until


class LargeReplyFormatter:
    def __init__(self, binding):
        from sim_platform.agents.tools.reads.formatter import ReadFormatter

        self.formatter = ReadFormatter(binding)

    def execute(self, captured):
        result = self.formatter.execute(captured)
        result['data']['test_padding'] = 'x' * (256 * 1024)
        return result


def request(config, number):
    return dict(
        run_id=config['run_id'],
        work_id='work-1',
        epoch='1',
        request_id='child-' + str(number),
        tool='market.quote',
        arguments={'symbol': 'AAPL'},
    )


def quote_program(client, config):
    response = client.request_tool(request(config, 1))
    if response['status'] != 'ok' or response['data']['symbol'] != 'AAPL':
        raise RuntimeError('wrong tool response')
    client.wait_stopped()


def roundtrip_program(client, config):
    for number in range(8):
        result = client.request_tool(request(config, number))
        if result != {'request_id': 'child-' + str(number), 'value': '100.0000'}:
            raise RuntimeError('response correlation failed')
    client.wait_stopped()


def cpu_program(client, config):
    client.request_tool(request(config, 1))
    deadline = time.monotonic() + 0.6
    value = 0
    while time.monotonic() < deadline:
        for index in range(10000):
            value = (value * 1664525 + index) & 0xFFFFFFFF
    client.request_tool(request(config, 2))
    client.wait_stopped()


def exit_program(client, config):
    os._exit(23)


def stuck_program(client, config):
    while True:
        time.sleep(0.05)  # Deliberately ignores cooperative stop.


def burst_nonreader(client, config):
    for number in range(32):
        client._outgoing.send_bytes(
            packet(dict(request=request(config, number)), client._limits.request_bytes + 512)
        )
    client.wait_stopped()


def oversize_program(client, config):
    client._outgoing.send_bytes(b'x' * (client._limits.request_bytes + 513))
    client.wait_stopped()


def malformed_program(client, config):
    client._outgoing.send_bytes(b'{"schema_version":"unknown"}')
    client.wait_stopped()


class ProcessHostTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        self.limits = TransportLimits(4096, 1024 * 1024, 4, 50)

    def submit(self, req):
        self.requests.append((time.monotonic(), req))
        future = Future()
        future.set_result({'result': dict(request_id=req['request_id'], value='100.0000')})
        return future

    def host(self, program, **options):
        host = AgentProcessHost(
            options.pop('submit', self.submit),
            {'run_id': 'run'},
            options.pop('limits', self.limits),
            program=program,
            **options,
        )
        self.addCleanup(host.close)
        return host

    def test_spawn_ready_does_not_run_before_play_and_correlates_replies(self):
        host = self.host(roundtrip_program)
        self.assertNotEqual(host.snapshot()['pid'], os.getpid())
        self.assertEqual(host.snapshot()['state'], 'ready')
        time.sleep(0.06)
        self.assertEqual(self.requests, [])
        host.play()
        until(lambda: host.snapshot()['transport']['delivered'] == 8)
        self.assertEqual(
            [r['request_id'] for _, r in self.requests], ['child-' + str(i) for i in range(8)]
        )
        # Strict no-burst admission spacing, measured on this receiver.
        times = [t for t, _ in self.requests]
        # Windows sleeps in ~15.6 ms timer ticks, so allow one tick of slack there.
        spacing = 0.004 if sys.platform == 'win32' else 0.018
        self.assertTrue(all(b - a >= spacing for a, b in zip(times, times[1:])))
        host.close()
        host.close()
        self.assertEqual(host.snapshot()['state'], 'closed')

    def test_nonreader_has_bounded_pending_and_close_unblocks_writer(self):
        def submit(req):
            future = Future()
            future.set_result({'result': {'data': 'x' * (256 * 1024)}})
            return future

        host = self.host(
            burst_nonreader, submit=submit, limits=TransportLimits(4096, 1024 * 1024, 2, 200)
        )
        host.play()
        until(lambda: host.snapshot()['transport']['received'] == 2)
        time.sleep(0.1)
        self.assertEqual(host.snapshot()['transport']['received'], 2)
        self.assertLessEqual(host.snapshot()['transport']['pending'], 2)
        started = time.monotonic()
        host.close()
        self.assertLess(time.monotonic() - started, 2)
        self.assertFalse(any(t.name.startswith('agent-ipc-') and t.is_alive() for t in threads()))

    def test_abrupt_exit_reports_failure_and_reaps_child(self):
        host = self.host(exit_program)
        host.play()
        until(lambda: host.snapshot()['state'] == 'failed')
        self.assertEqual(host.snapshot()['exitcode'], 23)
        self.assertIn('23', host.snapshot()['error'])
        pid = host.snapshot()['pid']
        host.close()
        self.assertNotIn(pid, [child.pid for child in multiprocessing.active_children()])

    def test_uncooperative_program_is_terminated_on_close(self):
        host = self.host(stuck_program, shutdown_grace=0.1)
        host.play()
        host.close()
        self.assertIsNotNone(host.snapshot()['exitcode'])

    def test_malformed_and_oversize_packets_fail_without_tool_access(self):
        for program in (malformed_program, oversize_program):
            with self.subTest(program=program.__name__):
                host = self.host(program)
                host.play()
                until(lambda: host.snapshot()['state'] == 'failed')
                self.assertEqual(self.requests, [])
                self.assertIsNotNone(host.snapshot()['error'])
                host.close()

    def test_start_failure_releases_pipes_without_running_tool(self):
        from multiprocessing.process import BaseProcess

        with patch.object(BaseProcess, 'start', side_effect=RuntimeError('spawn failed')):
            with self.assertRaisesRegex(RuntimeError, 'spawn failed'):
                self.host(quote_program)
        self.assertEqual(self.requests, [])

    def test_partial_bridge_start_reaps_started_child(self):
        from sim_platform.agents.hosting.bridge import ToolPipeBridge

        before = {child.pid for child in multiprocessing.active_children()}
        with patch.object(ToolPipeBridge, 'start', side_effect=RuntimeError('bridge failed')):
            with self.assertRaisesRegex(RuntimeError, 'bridge failed'):
                self.host(quote_program)
        self.assertEqual({child.pid for child in multiprocessing.active_children()}, before)

    def test_bridge_cleanup_failure_can_be_retried(self):
        host = self.host(roundtrip_program)
        with patch.object(host._bridge, 'close', side_effect=RuntimeError('bridge still draining')):
            with self.assertRaisesRegex(RuntimeError, 'bridge still draining'):
                host.close()
        host.close()
        self.assertEqual(host.snapshot()['state'], 'closed')

    def test_wire_rejects_nonfinite_json_and_unknown_versions(self):
        for raw in (
            b'{"schema_version":"agent-ipc/v1","value":NaN}',
            b'{"schema_version":"unknown"}',
            b'[]',
        ):
            with self.assertRaises(ContractError):
                unpack(raw)
        with self.assertRaises(ContractError):
            TransportLimits(4096, 4096, True)


class ProcessExperimentTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.app = Platform(self.root / 'accounts')
        self.addCleanup(self.app.close)
        self.profile = self.app.create_profile('Process fixture')
        self.account = self.app.create_account(self.profile, 'Cash', 100_000_000)
        self.source, _ = write_fixture(self.root / 'fixture')
        self.app.create_agent(self.profile, definition())
        self.start = 34_202_000_000_000
        self.config = dict(
            settings(), start_ns=str(self.start), end_ns=str(self.start + 5_000_000_000)
        )
        self.now = FakeTime()

    def open(self, program=None):
        def compose():
            return self.app.open_experiment(
                self.profile,
                self.account,
                method='agent',
                agent_id='demo',
                revision=1,
                source=self.source,
                settings=self.config,
                clock=ExperimentClock(self.start, self.now),
                execution_mode='controlled',
            )

        if program is None:
            return compose()

        def factory(*args, **kwargs):
            return AgentProcessHost(*args, program=program, **kwargs)

        with patch('sim_platform.agents.hosting.host.AgentProcessHost', factory):
            return compose()

    def test_platform_owns_idle_host_and_one_experiment_root(self):
        experiment = self.open()
        state = experiment.runner.snapshot()['agent_process']
        self.assertEqual(state['state'], 'ready')
        self.assertNotEqual(state['pid'], os.getpid())
        self.assertEqual(len(self.app._resources._roots), 1)
        self.assertEqual(state['transport']['received'], 0)
        self.app.close()
        self.assertEqual(experiment.runner.snapshot()['agent_process']['state'], 'closed')

    def test_real_read_is_json_recorded_and_archived_once(self):
        experiment = self.open(quote_program)
        experiment.play()
        until(lambda: experiment.runner.snapshot()['agent_process']['transport']['delivered'] == 1)
        experiment.stop()
        archive = self.app.agent_log_archive(self.profile, experiment.run_id)
        records = [r for r in archive['records'] if r['kind'] == 'tool']
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]['request']['request_id'], 'child-1')
        self.assertEqual(records[0]['result']['data']['symbol'], 'AAPL')
        self.assertEqual(
            records[0]['result']['cutoff']['market_position']['time_ns'], str(self.start)
        )

    def test_heavy_child_work_does_not_gate_market_or_eof_cleanup(self):
        experiment = self.open(cpu_program)
        experiment.play()
        until(lambda: experiment.runner.snapshot()['agent_process']['transport']['delivered'] == 1)
        self.now.ns = 6_000_000_000
        # Experiment end wins over Pause; notify the clock advance directly.
        experiment.runner.pipeline.wake()
        until(lambda: experiment.runner.pipeline.snapshot()['state'] == 'completed')
        # EOF closes the work channel before the heavy calculation returns.
        self.assertEqual(experiment.runner.snapshot()['agent_process']['transport']['received'], 1)
        until(lambda: experiment.runner.snapshot()['agent_process']['exitcode'] is not None)
        self.assertEqual(experiment.runner.pipeline.driver.sequence, 6)
        experiment.close()

    def test_agent_crash_does_not_stop_market(self):
        experiment = self.open(exit_program)
        experiment.play()
        until(lambda: experiment.runner.snapshot()['agent_process']['state'] == 'failed')
        self.assertEqual(experiment.runner.pipeline.snapshot()['state'], 'running')
        self.now.ns = 6_000_000_000
        # Experiment end wins over Pause; notify the clock advance directly.
        experiment.runner.pipeline.wake()
        until(lambda: experiment.runner.pipeline.snapshot()['state'] == 'completed')
        experiment.close()

    def test_crash_preserves_accepted_orders_until_normal_stop(self):
        experiment = self.open(exit_program)
        execution = experiment.runner.pipeline.driver.execution
        execution.submit(Intent('pending', 'AAPL', 'buy', 1, 1_000_000))
        experiment.play()
        until(lambda: experiment.runner.snapshot()['agent_process']['state'] == 'failed')
        self.assertEqual(execution.snapshot.account.orders[0].status, 'accepted')
        self.assertEqual(experiment.runner.pipeline.snapshot()['state'], 'running')
        experiment.stop()
        self.assertEqual(
            self.app.snapshot(self.profile, self.account).orders[0].status, 'cancelled'
        )

    def test_blocked_large_reply_does_not_block_native_market(self):
        from functools import partial
        from sim_platform.agents.tools.worker.host import ToolProcessHost

        self.config['limits']['pending_requests'] = 2
        host = partial(ToolProcessHost, formatter_factory=LargeReplyFormatter)
        with patch('sim_platform.agents.tools.server.router.ToolProcessHost', host):
            experiment = self.open(burst_nonreader)
            experiment.play()
            until(
                lambda: experiment.runner.snapshot()['agent_process']['transport']['received'] == 2
            )
            time.sleep(0.06)
            self.assertEqual(
                experiment.runner.snapshot()['agent_process']['transport']['received'], 2
            )
            self.now.ns = 3_000_000_000
            # Wake the deterministic-clock owner through its normal channel.
            experiment.runner.pipeline.wake()
            until(lambda: experiment.runner.pipeline.driver.sequence == 4)
            self.assertEqual(experiment.runner.pipeline.snapshot()['state'], 'running')
            self.assertEqual(
                experiment.runner.snapshot()['agent_process']['transport']['pending'], 2
            )
            experiment.stop()
            self.assertTrue(experiment.runner.closed)

    def test_failed_host_close_keeps_runner_owned_for_retry(self):
        experiment = self.open()
        host = experiment.runner._process
        with patch.object(host, 'close', side_effect=ContractError('host still draining')):
            with self.assertRaisesRegex(ContractError, 'host still draining'):
                experiment.stop()
        self.assertFalse(experiment.runner.closed)
        self.assertEqual(len(self.app._resources._roots), 1)
        experiment.stop()
        self.assertTrue(experiment.runner.closed)

    def test_failed_host_creation_cleans_execution_log_and_root(self):
        with patch(
            'sim_platform.agents.hosting.host.AgentProcessHost',
            side_effect=RuntimeError('host failed'),
        ):
            with self.assertRaisesRegex(RuntimeError, 'host failed'):
                self.open()
        self.assertEqual(self.app._resources._roots, {})
        self.assertEqual(self.app._resources._executions, {})
        self.assertEqual(self.app.runs(self.profile)[0]['status'], 'stopped')


if __name__ == '__main__':
    unittest.main()
