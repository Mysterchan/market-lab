"""Hand-calculated scheduling/deadlines and real spawn/owner integration."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from sim_platform import Platform, ContractError
from sim_platform.experiments.scheduling.clock import ExperimentClock
from support.clock import TestClock
from sim_platform.experiments.scheduling.policy.schedule import WorkSchedule
from sim_platform.experiments.scheduling.errors import WorkError
from sim_platform.experiments.scheduling.policy.attempts import AttemptPolicy
from sim_platform.agents.hosting.host import AgentProcessHost
from sim_platform.agents.tools.protocol.contracts import IMPLEMENTED_TOOLS, ToolError
from support.agents import definition, settings
from support.clock import FakeTime, until
from support.trading import fixture, ORIGIN, SECOND, wire
from support.tools import validator, SCHEMA


def position(now, sequence=1):
    return dict(time_ns=str(now), sequence=str(sequence), boundary='1', ordinal='1')


def schedule(mode='interval', busy='preserve_interval'):
    value = dict(mode=mode, busy_event_policy=busy, events=[])
    if mode == 'interval':
        value['interval_seconds'] = '30'
    if mode == 'event':
        value['events'] = [dict(kind='market_update', symbol='AAPL')]
    return value


def carrier_program(client, config):
    for number in range(2):
        work = client.next_work()
        if work['work_id'] != 'work-' + str(number + 1):
            raise RuntimeError('wrong work identity')
        request = wire(config, 'market.quote', dict(symbol='AAPL'), 'quote-' + str(number))
        request.update(work_id=work['work_id'], epoch=work['epoch'])
        if client.request_tool(request)['status'] != 'ok':
            raise RuntimeError('quote failed')
        request.update(request_id='finish-' + str(number), tool='control.finish', arguments={})
        if client.request_tool(request)['status'] != 'ok':
            raise RuntimeError('finish failed')
    client.wait_stopped()


class SchedulingTests(unittest.TestCase):
    def work(self, mode='interval', busy='preserve_interval'):
        work = WorkSchedule(schedule(mode, busy), 'AAPL', 2)
        self.observed = dict(sequence=1, orders={'own': 2})
        work.step(position(0), self.observed)
        return work

    def test_interval_begins_at_finish_not_initial_start(self):
        work = self.work()
        work.finish(10 * SECOND)
        self.assertEqual(work.due, 40 * SECOND)
        self.assertIsNone(work.step(position(39 * SECOND), self.observed))
        self.assertIsNotNone(work.step(position(40 * SECOND), self.observed))
        self.assertEqual(work.number, 2)
        self.assertIsNone(work.step(position(100 * SECOND), self.observed))

    def test_busy_event_policies_and_event_mode(self):
        for mode in ('interval', 'event'):
            for busy in ('preserve_interval', 'wake_on_pending_event'):
                work = self.work(mode, busy)
                work.schedule['events'] = [dict(kind='market_update', symbol='AAPL')]
                work.observe(dict(kind='market_update', symbol='AAPL'), position(1, 2))
                if mode == 'event':
                    work.step(position(1, 2), self.observed)
                    work.observe(dict(kind='market_update', symbol='AAPL'), position(2, 3))
                work.finish(2)
                result = work.step(position(2, 3), self.observed)
                self.assertEqual(
                    result is not None, mode == 'event' or busy == 'wake_on_pending_event'
                )
                if result is not None:
                    self.assertEqual(work.number, 2)
                    self.assertFalse(work.pending)

    def test_wait_overrides_interval_or_conditions_and_immediate_changed_state(self):
        work = self.work()
        work.wait(
            'w',
            [
                dict(kind='after', seconds='5'),
                dict(kind='order_changed', order_id='own', after_version='1'),
            ],
            10,
            self.observed,
        )
        self.assertIsNotNone(work.step(position(10), self.observed))
        self.assertEqual(work.number, 2)
        work.finish(10)
        self.assertEqual(work.due, 30 * SECOND + 10)

    def test_wait_validation_is_atomic_and_future_versions_rejected(self):
        work = self.work()
        for condition in (
            dict(kind='market_changed', symbol='AAPL', after_sequence='2'),
            dict(kind='order_changed', order_id='other', after_version='0'),
        ):
            with self.assertRaises(WorkError):
                work.wait('w', [dict(kind='after', seconds='1'), condition], 0, self.observed)
            self.assertEqual(work.state, 'running')
            self.assertEqual(work.conditions, [])

    def test_buckets_are_bounded_merge_and_record_overflow(self):
        work = self.work()
        work.schedule['events'] = [dict(kind='order_update')]
        for name in ('a', 'a', 'b', 'c'):
            work.observe(dict(kind='order_update', order_id=name), position(1))
        data = work.pending.take()
        self.assertEqual(len(data['events']), 2)
        self.assertEqual(data['events'][0]['count'], 2)
        self.assertEqual(data['overflow'], 1)

    def test_next_work_cannot_overlap_running_work(self):
        work = self.work()
        work.next_work()
        with self.assertRaises(WorkError):
            work.next_work()

    def test_stop_cancels_subscription_and_invalidates_epoch(self):
        work = self.work('event')
        result = work.next_work()
        work.end('stop')
        self.assertTrue(result.future.cancelled())
        self.assertEqual(work.epoch, 2)


class AttemptTests(unittest.TestCase):
    def setUp(self):
        self.now = FakeTime()
        self.clock = ExperimentClock(0, self.now)
        self.clock.resume()
        self.policy = AttemptPolicy(self.clock, '30', settings()['retry'])
        self.policy.begin()

    def test_pause_freezes_timeout_and_exact_deadline_reply_is_rejected(self):
        self.now.ns = 10 * SECOND
        self.clock.pause()
        self.now.ns = 100 * SECOND
        self.assertIsNone(self.policy.step(self.clock.target_ns()))
        self.clock.resume()
        self.now.ns = 120 * SECOND
        self.assertFalse(self.policy.receive('1', result={'text': 'late'}))
        self.assertEqual(self.policy.step(self.clock.target_ns())['event'], 'attempt_retry')

    def test_reply_before_deadline_survives_pause_and_delayed_owner(self):
        self.now.ns = 29 * SECOND
        self.assertTrue(self.policy.receive('1', result={'text': 'ok'}))
        self.clock.pause()
        self.now.ns = 500 * SECOND
        self.clock.resume()
        self.now.ns += 20 * SECOND
        outcome = self.policy.step(self.clock.target_ns())
        self.assertEqual(outcome['event'], 'attempt_completed')
        self.assertEqual(outcome['received_ns'], str(29 * SECOND))

    def test_retry_after_exponential_budget_and_stale_attempt(self):
        self.policy.receive('1', error='RATE_LIMIT', retry_after_seconds='5')
        event = self.policy.step(0)
        self.assertEqual(event['retry_due_ns'], str(5 * SECOND))
        self.assertFalse(self.policy.receive('1', result={'text': 'stale'}))
        self.assertIsNone(self.policy.step(4 * SECOND))
        self.policy.step(5 * SECOND)
        self.policy.receive('2', error='TRANSPORT_ERROR')
        self.assertEqual(self.policy.step(5 * SECOND)['retry_due_ns'], str(7 * SECOND))
        self.policy.step(7 * SECOND)
        self.policy.receive('3', error='PROVIDER_TEMPORARY')
        self.assertEqual(self.policy.step(7 * SECOND)['error'], 'RETRY_EXHAUSTED')

    def test_auth_and_context_errors_never_retry(self):
        for error in ('AUTH_FAILED', 'CONTEXT_TOO_LONG', 'MODEL_UNSUPPORTED'):
            if self.policy.state != 'running':
                self.policy.begin()
            self.policy.receive(self.policy.identity, error=error)
            self.assertEqual(self.policy.step(0)['error'], error)
            self.assertEqual(self.policy.state, 'failed')

    def test_retry_delay_freezes_during_pause_and_format_budget_is_finite(self):
        self.policy.receive('1', error='FORMAT_ERROR')
        self.assertEqual(self.policy.step(0)['retry_due_ns'], str(SECOND))
        self.clock.pause()
        self.now.ns = 100 * SECOND
        self.assertIsNone(self.policy.step(self.clock.target_ns()))
        self.clock.resume()
        self.now.ns += SECOND
        self.policy.step(self.clock.target_ns())
        self.policy.receive('2', error='FORMAT_ERROR')
        self.policy.step(self.clock.target_ns())
        self.now.ns += 2 * SECOND
        self.policy.step(self.clock.target_ns())
        self.policy.receive('3', error='FORMAT_ERROR')
        self.assertEqual(self.policy.step(self.clock.target_ns())['error'], 'RETRY_EXHAUSTED')

    def test_old_attempt_cannot_revive_after_new_request_or_stop(self):
        self.policy.receive('1', result={})
        self.policy.step(0)
        self.policy.begin()
        self.assertEqual(self.policy.identity, '2')
        self.assertFalse(self.policy.receive('1', result={}))
        self.policy.end()
        self.assertFalse(self.policy.receive('2', result={}))


class ControlIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = Platform(self.root / 'accounts')
        self.addCleanup(self.app.close)
        self.profile = self.app.create_profile('Controls')
        self.account = self.app.create_account(self.profile, 'Cash', 10_000_000)
        self.now = FakeTime()
        self.config = dict(
            settings(),
            start_ns=str(ORIGIN + 2 * SECOND),
            end_ns=str(ORIGIN + 8 * SECOND),
            granted_tools=sorted(IMPLEMENTED_TOOLS),
        )

    def open(self, mode='interval', program=None):
        value = definition()
        value['schedule'] = schedule(mode)
        if mode == 'interval':
            value['schedule']['interval_seconds'] = '1'
        self.app.create_agent(self.profile, value)
        if program is None:
            self.experiment = self.app.open_experiment(
                self.profile,
                self.account,
                method='agent',
                agent_id='demo',
                revision=1,
                source=fixture(self.root / 'source.bin'),
                settings=self.config,
                clock=TestClock(ORIGIN + 2 * SECOND, self.now),
                execution_mode='controlled',
            )
        else:
            original = AgentProcessHost

            def factory(*args, **kwargs):
                return original(*args, program=program, **kwargs)

            with patch('sim_platform.agents.hosting.host.AgentProcessHost', factory):
                self.experiment = self.app.open_experiment(
                    self.profile,
                    self.account,
                    method='agent',
                    agent_id='demo',
                    revision=1,
                    source=fixture(self.root / 'source.bin'),
                    settings=self.config,
                    clock=TestClock(ORIGIN + 2 * SECOND, self.now),
                    execution_mode='controlled',
                )
        self.runner = self.experiment.runner
        self.experiment.play()
        return self.experiment

    def call(self, tool, arguments=None, identity='r', work='work-1'):
        req = wire(dict(run_id=self.runner.run_id), tool, arguments or {}, identity)
        req['work_id'] = work
        future = self.runner.request_tool(req)
        self.now.ns += 1
        self.runner.pipeline.wake()
        return future.result(3)['result']

    def advance(self, seconds):
        self.now.ns += int(seconds * SECOND)
        self.runner.pipeline.wake()
        until(
            lambda: self.runner.pipeline.snapshot()['executed_ns']
            >= ORIGIN + 2 * SECOND + self.now.ns - 1
        )

    def test_finish_zero_trades_interval_dynamic_identity_and_duplicate_control(self):
        self.open()
        first = self.call('control.finish', identity='finish')
        self.assertTrue(validator.valid(first, SCHEMA['$defs']['ToolResponse'], SCHEMA), first)
        self.assertEqual(first['data']['next_due_ns'], str(ORIGIN + 3 * SECOND + 1))
        next_work = self.runner.next_work()
        self.advance(0.5)
        self.assertFalse(next_work.done())
        self.advance(0.5)
        self.assertEqual(next_work.result(2)['result']['work_id'], 'work-2')
        self.assertEqual(self.call('control.finish', identity='finish'), first)
        with self.assertRaises(ToolError):
            self.call('market.quote', dict(symbol='AAPL'), identity='stale')
        self.assertEqual(
            self.call('market.quote', dict(symbol='AAPL'), identity='fresh', work='work-2')[
                'status'
            ],
            'ok',
        )
        self.assertEqual(self.app.snapshot(self.profile, self.account).orders, ())

    def test_spawn_carrier_uses_two_work_identities_and_keeps_market_running(self):
        self.open(program=carrier_program)

        def first_finished():
            self.now.ns += 1
            self.runner.pipeline.wake()
            return self.runner.snapshot()['agent_process']['transport']['delivered'] >= 2

        until(first_finished)
        self.advance(1)

        def second_finished():
            self.now.ns += 1
            self.runner.pipeline.wake()
            return self.runner.snapshot()['agent_process']['transport']['delivered'] == 4

        until(second_finished)
        self.assertEqual(self.runner.snapshot()['agent_process']['state'], 'running')
        self.assertEqual(self.runner.snapshot()['work']['state'], 'idle')

    def test_event_carrier_first_activation_waits_for_market(self):
        self.open(mode='event')
        future = self.runner.next_work()
        self.assertFalse(future.done())
        self.advance(1)
        self.assertEqual(future.result(2)['result']['reason'], 'schedule')

    def test_pause_buffers_result_and_freezes_interval(self):
        self.open()
        self.call('control.finish', identity='finish')
        self.experiment.pause()
        future = self.runner.next_work()
        self.now.ns = 100 * SECOND
        self.runner.pipeline.wake()
        self.assertEqual(self.runner.pipeline.clock.target_ns(), ORIGIN + 2 * SECOND + 1)
        self.assertFalse(future.done())
        self.experiment.play()
        self.now.ns += SECOND
        self.runner.pipeline.wake()
        self.assertEqual(future.result(2)['result']['work_id'], 'work-2')

    def test_wait_for_order_version_wakes_on_fill_stop_only_cancels_remainder(self):
        self.open()
        accepted = self.call(
            'orders.submit_limit',
            dict(symbol='AAPL', side='buy', quantity_shares=5, limit_price_usd='100.0000'),
            identity='buy',
        )
        order = accepted['data']['order']
        self.call(
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
            identity='wait',
        )
        future = self.runner.next_work()
        self.advance(1)
        self.assertEqual(future.result(2)['result']['work_id'], 'work-2')
        self.experiment.close()
        final = self.app.snapshot(self.profile, self.account).orders[0]
        self.assertEqual(final.filled, 2)
        self.assertEqual(final.status, 'cancelled')
        self.experiment.close()

    def test_pending_tool_prevents_finish_without_forcing_trade(self):
        self.open()
        controls = self.runner._tools.controls
        controls.admit_request(dict(request_id='pending', work_id='work-1', epoch='1')).result(2)
        result = self.call('control.finish', identity='finish')
        self.assertEqual(result['error']['code'], 'WORK_HAS_PENDING_OPERATIONS')
        self.assertTrue(validator.valid(result, SCHEMA['$defs']['ToolResponse'], SCHEMA), result)
        controls.delivered('pending')
        self.assertEqual(self.call('control.finish', identity='finish'), result)
        self.assertEqual(self.call('control.finish', identity='finish-2')['status'], 'ok')

    def test_timeout_retry_fault_does_not_cancel_accepted_order(self):
        self.config['timeout_seconds'] = '0.5'
        self.config['retry']['max_attempts'] = 1
        self.open()
        self.call(
            'orders.submit_limit',
            dict(symbol='AAPL', side='buy', quantity_shares=5, limit_price_usd='90.0000'),
            identity='buy',
        )
        attempt = self.runner.begin_attempt().result(2)['result']['attempt_id']
        self.advance(0.5)
        until(lambda: self.runner.snapshot()['work']['attempt']['state'] == 'expiry_pending')
        self.runner._tools.controls.runtime.submit(
            'model_receipt',
            dict(
                attempt_id=attempt,
                received_ns=self.runner.pipeline.clock.target_ns(),
                error='MODEL_TIMEOUT',
            ),
        ).result(2)
        until(lambda: self.runner.snapshot()['work']['state'] == 'failed')
        self.assertFalse(self.runner.receive_attempt(attempt, result={'text': 'late'}))
        self.assertEqual(self.app.snapshot(self.profile, self.account).orders[0].status, 'accepted')
        self.advance(0.5)
        self.assertEqual(self.runner.pipeline.snapshot()['state'], 'running')

    def test_attempt_result_received_while_paused_adopted_only_on_resume(self):
        self.open()
        attempt = self.runner.begin_attempt().result(2)['result']['attempt_id']
        self.experiment.pause()
        self.now.ns = 100 * SECOND
        self.assertTrue(self.runner.receive_attempt(attempt, result={'text': 'ok'}))
        self.assertEqual(self.runner.snapshot()['work']['attempt']['state'], 'running')
        self.experiment.play()
        until(lambda: self.runner.snapshot()['work']['attempt']['state'] == 'completed')

    def test_expired_end_wins_over_pause_and_late_attempt(self):
        self.open()
        attempt = self.runner.begin_attempt().result(2)['result']['attempt_id']
        self.now.ns = 6 * SECOND
        self.experiment.pause()
        until(lambda: self.runner.pipeline.snapshot()['state'] == 'completed')
        self.assertFalse(self.runner.receive_attempt(attempt, result={'text': 'late'}))
        self.experiment.close()

    def test_crash_invalidates_work_preserves_order_and_is_json_recorded(self):
        self.open()
        self.call(
            'orders.submit_limit',
            dict(symbol='AAPL', side='buy', quantity_shares=5, limit_price_usd='90.0000'),
            identity='buy',
        )
        self.runner.agent_exited('failed', 'AGENT_CRASH')
        until(lambda: self.runner.snapshot()['work']['state'] == 'failed')
        with self.assertRaises(ToolError):
            self.call(
                'orders.submit_limit',
                dict(symbol='AAPL', side='buy', quantity_shares=1, limit_price_usd='90.0000'),
                identity='late',
            )
        self.assertEqual(len(self.app.snapshot(self.profile, self.account).orders), 1)
        self.advance(1)
        self.experiment.close()
        archive = self.app.agent_log_archive(self.profile, self.runner.run_id)
        self.assertTrue(any(r.get('event') == 'agent_failed' for r in archive['records']))

    def test_original_read_replay_keeps_cutoff_after_market_moves(self):
        self.open()
        first = self.call('market.quote', dict(symbol='AAPL'), identity='quote')
        self.advance(1)
        self.assertEqual(self.call('market.quote', dict(symbol='AAPL'), identity='quote'), first)
        changed = self.call('account.snapshot', identity='quote')
        self.assertEqual(changed['error']['code'], 'REQUEST_ID_CONFLICT')

    def test_invalid_wait_duration_has_no_state_effect(self):
        self.open()
        self.runner.next_work().result(2)
        for value in ('NaN', '1e2', '+1', '01', ' 1', '0', '0.0000000001'):
            with self.assertRaises(ToolError):
                self.call(
                    'control.wait',
                    dict(conditions=[dict(kind='after', seconds=value)]),
                    identity='invalid',
                )
            self.assertEqual(self.runner.snapshot()['work']['state'], 'running')

    def test_premature_next_work_is_operation_error_not_market_fault(self):
        self.open()
        self.runner.next_work().result(2)
        with self.assertRaisesRegex(ContractError, 'finish current work'):
            self.runner.next_work().result(2)
        self.assertEqual(self.runner.pipeline.snapshot()['state'], 'running')

    def test_wait_identifier_is_bounded_and_response_budget_precedes_state_change(self):
        self.open()
        self.runner._tools.binding['limits']['tool_response_bytes'] = 1
        # Trusted injected bound exercises refusal before any timer side effect.
        result = self.call(
            'control.wait', dict(conditions=[dict(kind='after', seconds='1')]), identity='small'
        )
        self.assertEqual(result['error']['code'], 'OUTPUT_TOO_LARGE')
        self.assertEqual(self.runner.snapshot()['work']['state'], 'running')
        self.runner._tools.binding['limits']['tool_response_bytes'] = 1048576
        result = self.call(
            'control.wait', dict(conditions=[dict(kind='after', seconds='1')]), identity='x' * 128
        )
        self.assertTrue(validator.valid(result, SCHEMA['$defs']['ToolResponse'], SCHEMA), result)
        self.assertLessEqual(len(result['data']['wait_id']), 128)

    def test_stop_pending_subscription_and_json_control_evidence(self):
        self.open(mode='event')
        future = self.runner.next_work()
        self.experiment.close()
        with self.assertRaises(ContractError):
            future.result(2)
        archive = self.app.agent_log_archive(self.profile, self.runner.run_id)
        self.assertEqual(archive['end']['status'], 'stopped')


if __name__ == '__main__':
    unittest.main()
