"""Spawn read formatting cannot own the market or publish canceled/paused work."""

from threading import current_thread
import unittest
from unittest.mock import patch

from sim_platform import ContractError
from sim_platform.application.experiments.agent_driver import AgentToolCapture
from support.clock import until
from support.tools import request


from support.async_reads import AsyncReadFixture, FaultFormatter


class AsyncAgentReadTests(unittest.TestCase, AsyncReadFixture):
    def setUp(self):
        self.prepare(self.addCleanup)

    def query(self, experiment, number='r1'):
        return experiment.request_tool(
            request('market.quote', {'symbol': 'AAPL'}, number=number, run=experiment.run_id)
        )

    def test_slow_formatting_allows_market_and_pause_with_original_cutoff(self):
        capture = AgentToolCapture.__call__

        def capture_on_owner(adapter, tool):
            self.assertEqual(current_thread().name, 'execution-owner')
            return capture(adapter, tool)

        with patch.object(AgentToolCapture, '__call__', capture_on_owner):
            experiment = self.open()
            future = self.query(experiment)
            self.assertTrue(self.entered.wait(2))
            self.now.ns += 2_000_000_000
            experiment.runner.pipeline._wake.set()
            until(lambda: experiment.market.time_ns == 34_204_000_000_000)
            experiment.pause()
            self.release.set()
            self.assertEqual(experiment.view()['pipeline']['state'], 'paused')
            self.assertFalse(future.done())
            experiment.play()
            response = future.result(2)['result']
            self.assertEqual(response['cutoff']['market_position']['time_ns'], str(self.origin))
            self.assertIsNone(response['data']['last'])
            experiment.stop()
        archive = self.app.agent_log_archive(self.profile, experiment.run_id)
        self.assertEqual(len([r for r in archive['records'] if r['kind'] == 'tool']), 1)
        self.assertTrue(any(r.get('event') == 'work_started' for r in archive['records']))
        ordinals = [int(r['position']['ordinal']) for r in archive['records']]
        self.assertEqual(ordinals, sorted(ordinals))

    def test_stop_and_eof_cancel_slow_reads_and_late_work_cannot_write(self):
        for end in ('stop', 'eof'):
            with self.subTest(end=end):
                self.entered.clear()
                self.release.clear()
                experiment = self.open()
                future = self.query(experiment)
                self.assertTrue(self.entered.wait(2))
                if end == 'stop':
                    experiment.stop()
                else:
                    self.now.ns += 5_000_000_000
                    experiment.runner.pipeline._wake.set()
                    experiment.runner.pipeline.channels.ended.result(2)
                with self.assertRaisesRegex(ContractError, 'before result'):
                    future.result(2)
                log = self.app.directory / 'agent-logs' / (experiment.run_id + '.jsonl')
                before = log.read_bytes()
                self.release.set()
                experiment.close()
                self.assertFalse(experiment.runner._tools.snapshot()['alive'])
                self.assertEqual(log.read_bytes(), before)
                archive = self.app.agent_log_archive(self.profile, experiment.run_id)
                self.assertFalse(any(r['kind'] == 'tool' for r in archive['records']))

    def test_capacity_includes_captured_reads_until_output_or_cancellation(self):
        self.config['limits'] = dict(self.config['limits'], pending_requests=2)
        experiment = self.open()
        first = self.query(experiment)
        self.assertTrue(self.entered.wait(2))
        second = self.query(experiment, 'r2')
        with self.assertRaisesRegex(ContractError, 'capacity'):
            self.query(experiment, 'r3')
        experiment.stop()
        for future in (first, second):
            with self.assertRaises(ContractError):
                future.result(2)

    def test_formatter_fault_is_visible_and_terminates_owner(self):
        experiment = self.open(FaultFormatter)
        with self.assertRaisesRegex(RuntimeError, 'query fault'):
            self.query(experiment).result(2)
        until(lambda: experiment.view()['pipeline']['state'] == 'failed')
        self.assertEqual(self.app.run(self.profile, experiment.run_id)['status'], 'failed')
        with self.assertRaisesRegex(ContractError, 'output failed'):
            experiment.close()
        self.assertEqual(self.app._resources._roots, {})

    def test_stop_drains_committed_finance_behind_stuck_read_without_resubmit(self):
        self.config['granted_tools'].append('orders.submit_limit')
        experiment = self.open()
        reading = self.query(experiment)
        self.assertTrue(self.entered.wait(2))
        buying = experiment.request_tool(
            request(
                'orders.submit_limit',
                dict(symbol='AAPL', side='buy', quantity_shares=1, limit_price_usd='100.0000'),
                number='committed-behind-read',
                run=experiment.run_id,
            )
        )
        self.now.ns += 1
        experiment.runner.pipeline.wake()
        until(lambda: len(self.app.snapshot(self.profile, self.account).orders) == 1)
        self.assertFalse(buying.done())
        experiment.stop()
        with self.assertRaises(ContractError):
            reading.result(2)
        accepted = buying.result(2)['result']
        self.assertEqual(accepted['data']['order']['status'], 'accepted')
        state = self.app.snapshot(self.profile, self.account)
        self.assertEqual(len(state.orders), 1)
        self.assertEqual(state.orders[0].status, 'cancelled')
        archive = self.app.agent_log_archive(self.profile, experiment.run_id)
        records = [r for r in archive['records'] if r['kind'] == 'tool']
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]['result'], accepted)

    def test_child_crash_returns_unavailable_but_market_continues(self):
        experiment = self.open()
        future = self.query(experiment)
        self.assertTrue(self.entered.wait(2))
        child = experiment.runner._tools._worker._process
        child.terminate()
        child.join(2)
        self.assertEqual(future.result(2)['result']['error']['code'], 'TOOL_UNAVAILABLE')
        self.now.ns += 2_000_000_000
        experiment.runner.pipeline._wake.set()
        until(lambda: experiment.market.time_ns == 34_204_000_000_000)
        self.assertEqual(experiment.view()['pipeline']['state'], 'running')
        self.assertEqual(
            self.query(experiment, 'after-crash').result(2)['result']['error']['code'],
            'TOOL_UNAVAILABLE',
        )
        experiment.stop()
