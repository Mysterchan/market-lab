"""Independent Runtime expectations and real spawn/native integration."""

import ast
import json
import multiprocessing
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import time
import unittest

from sim_platform import Platform, ContractError
from sim_platform.experiments.scheduling.policy.controller import LiveWorkPolicy
from sim_platform.experiments.scheduling.clock import SharedExperimentClock, monotonic_ns
from sim_platform.experiments.scheduling.host import ExperimentRuntimeHandle, RuntimeError
from sim_platform.experiments.scheduling.protocol import command, packet, unpack
from support.agents import definition, settings
from support.scheduling import configuration, ManualClock
from support.clock import until
from support.trading import fixture, ORIGIN, SECOND

from support.failures import expected_child_failure

ROOT = Path(__file__).resolve().parents[2]


def fail_start(config, clock, notice):
    raise ContractError('intentional runtime startup failure')


def direct_carrier(client, report):
    try:
        first = client.next_work()
        due = client.finish(first)['next_due_ns']
        report.send(dict(ready=True, due_ns=due))
        second = client.next_work()
        report.send(dict(work=second, received_ns=client.target_ns()))
    finally:
        client.close()
        report.close()


class RuntimeContractTests(unittest.TestCase):
    def setUp(self):
        self.clock = ManualClock()
        self.notices = []
        self.policy = LiveWorkPolicy(
            configuration('30'),
            self.clock,
            lambda event, details: self.notices.append((event, details)),
        )
        self.identity = dict(work_id='work-1', epoch='1')
        self.policy.end_ns = 1000 * SECOND
        self.policy.step()

    def test_late_agent_disconnect_preserves_terminal_work_and_notices(self):
        self.policy.end('configured session end')
        snapshot, notices = self.policy.snapshot(), list(self.notices)
        result = self.policy.dispatch('agent_failed', dict(reason='work capability disconnected'))
        self.assertEqual(result, dict(failed=False))
        self.assertEqual(self.policy.snapshot(), snapshot)
        self.assertEqual(self.notices, notices)
        with self.assertRaisesRegex(ContractError, 'runtime has ended'):
            self.policy.dispatch('admit', dict(self.identity, request_id='late'))

    def test_hand_calculated_finish_deadline_and_no_market_position(self):
        initial = self.policy.dispatch('next_work', {})
        self.assertEqual(initial['scheduled_due_ns'], '0')
        self.assertNotIn('position', initial)
        self.clock.ns = 10 * SECOND
        result = self.policy.dispatch('finish', self.identity)
        self.assertEqual(result['next_due_ns'], str(40 * SECOND))
        waiter = self.policy.dispatch('next_work', {})
        self.clock.ns = 39 * SECOND
        self.policy.step()
        self.assertFalse(waiter.done())
        self.clock.ns = 41 * SECOND
        self.policy.step()
        self.assertEqual(waiter.result()['scheduled_due_ns'], str(40 * SECOND))
        self.assertEqual(waiter.result()['dispatched_ns'], str(41 * SECOND))

    def test_wait_before_and_after_notification_single_wake(self):
        self.policy.dispatch('next_work', {})
        observation = dict(
            event_sequence=1,
            observation=dict(sequence=1, orders={'own': 3}),
            events=[dict(kind='order_update', order_id='own')],
        )
        self.policy.dispatch('observe', observation)
        self.policy.dispatch(
            'wait',
            dict(
                self.identity,
                request_id='w',
                conditions=[dict(kind='order_changed', order_id='own', after_version='2')],
            ),
        )
        self.policy.step()
        self.assertEqual(self.policy.work.number, 2)
        self.assertTrue(self.policy.dispatch('observe', observation)['duplicate'])
        self.policy.step()
        self.assertEqual(self.policy.work.number, 2)

    def test_wait_registered_first_wakes_only_after_new_version(self):
        self.policy.dispatch('next_work', {})
        self.policy.dispatch(
            'observe',
            dict(event_sequence=1, observation=dict(sequence=1, orders={'own': 3}), events=[]),
        )
        self.policy.dispatch(
            'wait',
            dict(
                self.identity,
                request_id='w',
                conditions=[dict(kind='order_changed', order_id='own', after_version='3')],
            ),
        )
        waiter = self.policy.dispatch('next_work', {})
        self.policy.step()
        self.assertFalse(waiter.done())
        self.policy.dispatch(
            'observe',
            dict(
                event_sequence=2,
                observation=dict(sequence=2, orders={'own': 4}),
                events=[dict(kind='order_update', order_id='own')],
            ),
        )
        self.policy.step()
        self.assertEqual(waiter.result()['work_id'], 'work-2')
        self.policy.step()
        self.assertEqual(self.policy.work.number, 2)

    def test_gap_and_invalid_wait_are_rejected_before_mutation(self):
        with self.assertRaises(ContractError):
            self.policy.dispatch(
                'observe',
                dict(event_sequence=2, observation=dict(sequence=1, orders={}), events=[]),
            )
        self.assertEqual(self.policy.snapshot()['event_sequence'], 0)
        with self.assertRaises(ContractError):
            self.policy.dispatch(
                'wait',
                dict(self.identity, request_id='w', conditions=[dict(kind='after', seconds='NaN')]),
            )
        self.assertEqual(self.policy.work.state, 'running')

    def test_duplicate_then_gap_keeps_authoritative_baseline(self):
        notification = dict(event_sequence=1, observation=dict(sequence=1, orders={}), events=[])
        self.policy.dispatch('observe', notification)
        self.assertTrue(self.policy.dispatch('observe', notification)['duplicate'])
        with self.assertRaises(ContractError):
            self.policy.dispatch('observe', dict(notification, event_sequence=3))
        self.assertEqual(self.policy.snapshot()['event_sequence'], 1)

    def test_pending_operations_and_model_attempt_prevent_finish(self):
        self.policy.dispatch('admit', dict(self.identity, request_id='tool'))
        with self.assertRaises(ContractError):
            self.policy.dispatch('finish', self.identity)
        self.policy.dispatch('deliver', dict(request_id='tool'))
        self.policy.dispatch('begin_attempt', self.identity)
        with self.assertRaises(ContractError):
            self.policy.dispatch('finish', self.identity)

    def test_pause_and_timeout_exact_boundary(self):
        self.policy.dispatch('begin_attempt', self.identity)
        self.clock.ns = 50_000_000
        self.clock.running = False
        self.policy.step()
        self.assertEqual(self.policy.attempts.state, 'running')
        self.clock.ns = 100_000_000
        self.assertFalse(
            self.policy.dispatch('receive_attempt', dict(attempt_id='1', result={}))['accepted']
        )
        self.clock.running = True
        self.policy.step()
        self.assertEqual(self.policy.attempts.state, 'retrying')

    def test_fault_and_stop_invalidate_identity_and_waiter(self):
        self.policy.dispatch('finish', self.identity)
        waiter = self.policy.dispatch('next_work', {})
        self.policy.end('stop')
        self.assertTrue(waiter.cancelled())
        with self.assertRaises(ContractError):
            self.policy.dispatch('finish', self.identity)

    def test_wire_rejects_extra_fields_secret_and_nonfinite(self):
        valid = dict(id='1', command='finish', data=self.identity)
        self.assertEqual(command(unpack(packet(valid))), valid)
        for bad in (
            dict(valid, version='v0'),
            dict(valid, data=dict(self.identity, api_key='secret')),
            dict(valid, data=[]),
        ):
            with self.assertRaises(ContractError):
                command(bad)
        with self.assertRaises(ContractError):
            unpack(b'{"value":NaN}')

    def test_scheduling_cases_are_unique_and_have_explicit_expected_results(self):
        cases = json.loads(
            (ROOT / 'tests/fixtures/agent-contracts/scheduling-cases.json').read_text()
        )
        self.assertEqual(len(cases['cases']), 12)
        self.assertEqual(len({case['id'] for case in cases['cases']}), 12)
        for case in cases['cases']:
            self.assertTrue(case['input'])
            self.assertTrue(case['expected'])


class RuntimeProcessTests(unittest.TestCase):
    def host(self, **options):
        host = ExperimentRuntimeHandle(LiveWorkPolicy, configuration(), **options)
        self.addCleanup(host.close)
        host.clock.resume()
        return host

    def test_observers_bind_once_and_receive_child_failure(self):
        host = self.host()
        failures = []
        host.bind_callbacks(on_notice=lambda event, details: None, on_failure=failures.append)
        with self.assertRaises(ContractError):
            host.bind_callbacks(on_notice=lambda event, details: None, on_failure=failures.append)
        host._process.terminate()
        until(lambda: bool(failures))
        host.close()
        self.assertFalse(host.snapshot()['alive'])

    def test_real_spawn_timer_and_common_posix_clock(self):
        host = self.host()
        first = host.submit('next_work').result(2)
        self.assertEqual(first['work_id'], 'work-1')
        identity = {key: first[key] for key in ('work_id', 'epoch')}
        finished = host.submit('finish', identity).result(2)
        due = int(finished['next_due_ns'])
        second = host.submit('next_work').result(2)
        self.assertEqual(second['work_id'], 'work-2')
        self.assertEqual(int(second['scheduled_due_ns']), due)
        self.assertGreaterEqual(int(second['dispatched_ns']), due)
        self.assertLess(abs(host.clock.target_ns() - int(second['dispatched_ns'])), SECOND)
        self.assertGreater(monotonic_ns(), 0)

    def test_direct_agent_delivery_bypasses_parent_cpu_and_capability_is_narrow(self):
        host = self.host()
        client = host.work_client()
        with self.assertRaises(ContractError):
            host.work_client()
        context = multiprocessing.get_context('spawn')
        receiver, sender = context.Pipe(duplex=False)
        child = context.Process(target=direct_carrier, args=(client, sender))
        child.start()
        sender.close()
        try:
            self.assertTrue(receiver.poll(2))
            ready = receiver.recv()
            deadline = time.perf_counter() + 0.15
            value = 0
            while time.perf_counter() < deadline:
                value = (value * 3 + 1) % 1_000_003
            self.assertTrue(receiver.poll(2))
            delivered = receiver.recv()
            self.assertEqual(delivered['work']['work_id'], 'work-2')
            self.assertLess(delivered['received_ns'] - int(ready['due_ns']), 100_000_000)
        finally:
            child.join(1)
            if child.is_alive():
                child.terminate()
                child.join(1)
            receiver.close()
            client.close()
        self.assertEqual(child.exitcode, 0)

    def test_direct_client_cannot_observe_or_control_attempts(self):
        host = self.host()
        client = host.work_client()
        try:
            with self.assertRaisesRegex(ContractError, 'CONTRACT_ERROR'):
                client._request(
                    'observe',
                    dict(event_sequence=1, observation=dict(sequence=1, orders={}), events=[]),
                )
            self.assertEqual(client.next_work()['work_id'], 'work-1')
        finally:
            client.close()

    def test_shared_clock_pause_freezes_child_timer(self):
        host = self.host()
        work = host.submit('next_work').result(2)
        host.submit('finish', {k: work[k] for k in ('work_id', 'epoch')}).result(2)
        host.clock.pause()
        frozen = host.clock.target_ns()
        future = host.submit('next_work')
        time.sleep(0.1)
        self.assertFalse(future.done())
        self.assertEqual(host.clock.target_ns(), frozen)
        host.clock.resume()
        self.assertEqual(future.result(2)['work_id'], 'work-2')

    def test_bounded_pending_and_reserved_stop(self):
        host = self.host(capacity=1)
        work = host.submit('next_work').result(2)
        host.submit('finish', {k: work[k] for k in ('work_id', 'epoch')}).result(2)
        host.clock.pause()
        pending = host.submit('next_work')
        with self.assertRaises(RuntimeError) as raised:
            host.submit('inspect')
        self.assertEqual(raised.exception.code, 'QUEUE_FULL')
        host.close()
        self.assertFalse(host.snapshot()['alive'])
        with self.assertRaises(ContractError):
            pending.result(1)
        host.close()

    def test_child_crash_reports_failure_and_releases_pending(self):
        errors = []
        host = self.host(on_failure=errors.append)
        work = host.submit('next_work').result(2)
        host.submit('finish', {k: work[k] for k in ('work_id', 'epoch')}).result(2)
        host.clock.pause()
        future = host.submit('next_work')
        host._process.terminate()
        with self.assertRaises(ContractError):
            future.result(2)
        until(lambda: bool(errors))
        host.close()

    @expected_child_failure(
        'sim_platform.domain.models.ContractError: intentional runtime startup failure'
    )
    def test_startup_failure_and_malformed_input_leave_no_live_child(self):
        before = {p.pid for p in multiprocessing.active_children()}
        with self.assertRaises(ContractError):
            ExperimentRuntimeHandle(fail_start, configuration(), startup_timeout=1)
        self.assertEqual({p.pid for p in multiprocessing.active_children()}, before)
        host = self.host()
        host.submit('next_work').result(2)
        with self.assertRaises(ContractError):
            host.submit('finish', dict(work_id='work-1', epoch='1', unexpected=True))
        self.assertEqual(host.submit('inspect').result(2)['work']['state'], 'running')

    def test_policy_operation_error_does_not_crash_runtime(self):
        host = self.host()
        host.submit('next_work').result(2)
        with self.assertRaises(RuntimeError) as raised:
            host.submit('next_work').result(2)
        self.assertEqual(raised.exception.code, 'WORK_HAS_PENDING_OPERATIONS')
        self.assertTrue(host.snapshot()['alive'])

    @expected_child_failure(
        'sim_platform.domain.models.ContractError: shared clock unavailable; possible runtime failure',
        required=False,
    )
    def test_clock_lock_failure_is_bounded_and_close_needs_no_clock_lock(self):
        host = self.host()
        host.submit('next_work').result(2)
        self.assertTrue(host.clock._lock.acquire(timeout=1))
        try:
            started = time.monotonic()
            with self.assertRaises(ContractError):
                host.clock.target_ns()
            self.assertLess(time.monotonic() - started, 0.5)
            host.close()
            self.assertFalse(host.snapshot()['alive'])
        finally:
            host.clock._lock.release()

    def test_close_reports_blocked_callback_and_retries_after_release(self):
        entered, release = Event(), Event()

        def hold(event, details):
            if event == 'work_started':
                entered.set()
                release.wait(3)

        host = self.host(on_notice=hold)
        try:
            self.assertTrue(entered.wait(1))
            with self.assertRaisesRegex(ContractError, 'IO did not close'):
                host.close()
            self.assertFalse(host.snapshot()['closed'])
            self.assertFalse(host.snapshot()['alive'])
        finally:
            release.set()
            host.close()


class RuntimeNativeIntegrationTests(unittest.TestCase):
    def test_platform_factory_and_dispatch_while_actual_owner_is_blocked(self):
        with TemporaryDirectory() as directory, Platform(Path(directory) / 'app') as app:
            profile = app.create_profile('runtime')
            account = app.create_account(profile, 'cash', 10_000_000)
            app.create_agent(profile, definition())
            config = dict(
                settings(), start_ns=str(ORIGIN + 2 * SECOND), end_ns=str(ORIGIN + 8 * SECOND)
            )
            clock = SharedExperimentClock(
                multiprocessing.get_context('spawn'), int(config['start_ns'])
            )
            experiment = app.open_experiment(
                profile,
                account,
                source=fixture(Path(directory) / 'source.bin'),
                method='agent',
                agent_id='demo',
                revision=1,
                settings=config,
                clock=clock,
                execution_mode='controlled',
            )
            prepared = app.agent_experiment(profile, experiment.runner.experiment_id)['config']
            prepared['definition']['schedule']['interval_seconds'] = '0.05'
            notices = []
            runtime = app._new_experiment_runtime(
                prepared,
                clock=clock,
                prepared_artifact=experiment.market.reader.directory,
                on_notice=lambda event, details: notices.append((event, details)),
            )
            entered, release = Event(), Event()
            try:
                pipeline = experiment.runner.pipeline
                old_operation = pipeline.operation

                def blocked(request, position):
                    if request.get('probe'):
                        entered.set()
                        if not release.wait(2):
                            raise ContractError('blocked owner test exceeded bound')
                        return dict(done=True)
                    return old_operation(request, position)

                pipeline.operation = blocked
                experiment.play()
                first = runtime.submit('next_work').result(2)
                runtime.submit('finish', {k: first[k] for k in ('work_id', 'epoch')}).result(2)
                waiting = runtime.submit('next_work')
                operation = pipeline.submit(dict(probe=True))
                self.assertTrue(entered.wait(1))
                next_work = waiting.result(1)
                self.assertEqual(next_work['work_id'], 'work-2')
                self.assertFalse(operation.done())
                self.assertNotIn('position', next_work)
                release.set()
                self.assertTrue(operation.result(2)['result']['done'])
                self.assertEqual(app.snapshot(profile, account).orders, ())
            finally:
                release.set()
                runtime.close()
                experiment.close()

    def test_runtime_core_does_not_import_market_sql_or_agent_code(self):
        forbidden = (
            'sim_platform.agents',
            'sim_platform.market',
            'sim_platform.execution',
            'sim_platform.experiments.execution',
            'sim_platform.persistence',
            'sim_platform.application',
            'sqlalchemy',
            'PySide6',
        )
        paths = list((ROOT / 'sim_platform/experiments/scheduling').glob('*.py'))
        self.assertGreaterEqual(len(paths), 7)
        for path in paths:
            for node in ast.walk(ast.parse(path.read_text())):
                names = (
                    [alias.name for alias in node.names]
                    if isinstance(node, ast.Import)
                    else [node.module or ''] if isinstance(node, ast.ImportFrom) else []
                )
                self.assertFalse(any(name.startswith(forbidden) for name in names), path)


if __name__ == '__main__':
    unittest.main()
