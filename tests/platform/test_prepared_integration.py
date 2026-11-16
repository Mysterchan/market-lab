"""Prepared experiment integration, lifecycle, tools, model and Replay verification."""

from pathlib import Path
import struct
import sys
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tests'))
from itch_fixtures import directory, add, common, frame
from support.prepared_market import PreparedMarketFixture
from support.models import AgentModelFixture, ChildModelProvider
from sim_platform.domain.models import ContractError
from sim_platform.market.models import FutureDataError
from sim_platform.market.prepared.builder import build
from sim_platform.market.prepared.reader import PreparedMarket
from sim_platform.market.prepared.session import PreparedSession
from sim_platform.persistence.prepared.store import ImportLease


class PreparedIntegrationTests(PreparedMarketFixture, unittest.TestCase):

    def open_session(self):
        session = PreparedSession(self.store)
        self.addCleanup(session.close)
        session.load(self.source)
        deadline = time.monotonic() + 10
        while not session.prepare():
            if time.monotonic() > deadline:
                raise TimeoutError('import did not finish')
            time.sleep(0.005)
        return session

    def test_reuse_opens_independent_cursors_without_another_import(self):
        first = self.open_session()
        first.start_position('first_book')
        self.assertEqual(first.time_ns, 3)
        with patch(
            'sim_platform.persistence.prepared.store.PreparationJob',
            side_effect=AssertionError('no rebuild'),
        ):
            second = self.open_session()
        self.assertEqual(second.snapshot.sequence, 0)
        first.step()
        self.assertEqual(second.snapshot.sequence, 0)
        first.close()
        self.assertEqual(second.step().sequence, 1)

    def test_indexed_history_is_asof_and_corrections_do_not_rewrite_past(self):
        target = self.root / 'prepared'
        build(self.source, target)
        with PreparedMarket(target, verify=True) as reader:
            p = reader.prefix
            before = reader.history.trades(p(20), 20)
            after = reader.history.trades(p(30), 30)
            self.assertEqual([x.match_id for x in before], [1, 2])
            self.assertEqual([x.match_id for x in after], [2])
            bar = reader.history.bars(p(20), 20)[0]
            self.assertEqual(
                (bar.open, bar.high, bar.low, bar.close, bar.volume, bar.count, bar.notional),
                (100, 110, 100, 110, 5, 2, 530),
            )
            corrected = reader.history.bars(p(30), 30)[0]
            self.assertEqual((corrected.open, corrected.low, corrected.volume), (110, 110, 3))
            self.assertFalse(corrected.complete)
            bars = reader.history.bars(p(60_000_000_000), 60_000_000_000)
            self.assertEqual(len(bars), 2)
            self.assertTrue(bars[0].complete)
            # Index queries do not decode source or mutate artifacts.
            self.assertEqual(reader.history.bars(p(20), 20)[0], bar)

    def test_start_position_ties_and_owner_lease_prevent_mutable_seek(self):
        session = self.open_session()
        session.start_position('custom', 20)
        self.assertEqual(session.snapshot.sequence, 5)
        marker = object()
        session.claim_execution(marker)
        with self.assertRaises(ContractError):
            session.seek(0)
        with self.assertRaises(ContractError):
            session.close()
        session.release_execution(marker)
        with self.assertRaises(FutureDataError):
            session.book(as_of_ns=30)
        self.assertEqual(session.book(as_of_ns=2).bids, ((99, 20),))

    def test_import_lock_rejects_concurrent_inventory_writer(self):
        lease = ImportLease(self.store.directory)
        self.addCleanup(lease.close)
        with self.assertRaises(ContractError):
            self.store.begin(self.source)
        lease.close()
        session = self.open_session()
        self.assertEqual(session.status, 'ready')

    def test_changed_source_is_not_reused_and_failed_import_does_not_register(self):
        session = self.open_session()
        session.close()
        old = (self.store.directory / 'inventory.json').read_bytes()
        self.source.write_bytes(self.source.read_bytes()[:-1])
        failed = PreparedSession(self.store)
        self.addCleanup(failed.close)
        failed.load(self.source)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                ready = failed.prepare()
            except ContractError:
                break
            self.assertFalse(ready)
            time.sleep(0.005)
        else:
            self.fail('failure was not reported')
        self.assertEqual((self.store.directory / 'inventory.json').read_bytes(), old)
        failed.close()
        self.assertFalse(list(self.store.directory.glob('.preparation-job-*')))

    def test_missing_symbol_and_bad_history_fail_before_publication(self):
        self.source.write_bytes(
            directory('AAPL', timestamp=1)
            + frame(common('B', timestamp=30) + struct.pack('>Q', 999))
            + b'\0\0'
        )
        target = self.root / 'bad'
        with self.assertRaises(ContractError):
            build(self.source, target)
        self.assertFalse(target.exists())

    def test_nonprintable_execution_and_printable_execution_can_share_a_match(self):
        from itch_fixtures import reduce

        nonprint = frame(
            common('C', timestamp=4)
            + struct.pack('>QI', 1, 2)
            + struct.pack('>Q', 123)
            + b'N'
            + struct.pack('>I', 101)
        )
        self.source.write_bytes(
            directory('AAPL', timestamp=1)
            + add(1, 'B', 5, 99, 'AAPL', timestamp=2)
            + add(2, 'S', 5, 101, 'AAPL', timestamp=3)
            + nonprint
            + reduce('E', 2, 2, timestamp=4)
            + frame(common('B', timestamp=5) + struct.pack('>Q', 123))
            + b'\0\0'
        )
        target = self.root / 'paired'
        build(self.source, target)
        with PreparedMarket(target, verify=True) as reader:
            history = reader.history
            self.assertEqual(len(history.trades(reader.prefix(4), 4)), 1)
            self.assertEqual(len(history.trades(reader.prefix(4), 4, include_nonprintable=True)), 2)
            self.assertEqual(history.bars(reader.prefix(4), 4)[0].volume, 2)
            self.assertEqual(history.trades(reader.prefix(5), 5, include_nonprintable=True), ())
            self.assertEqual(history.bars(reader.prefix(5), 5), ())


class ManualClock:
    def __init__(self, stamp):
        self.stamp = stamp
        self.running = False

    def target_ns(self):
        return self.stamp

    def resume(self):
        self.running = True

    def pause(self):
        self.running = False


class ExperimentKernelTests(unittest.TestCase):
    setUp = PreparedMarketFixture.setUp
    open_session = PreparedIntegrationTests.open_session

    def kernel(self, operation, *, start=3, end=60_000_000_001):
        from sim_platform.experiments.execution.session import ExperimentSession

        market = self.open_session()
        market.seek(start)
        clock = ManualClock(start)
        messages = []

        class Driver:
            @property
            def market(self):
                return market

            @property
            def sequence(self):
                return market.snapshot.sequence

            def play(self):
                market.play()

            def pause(self):
                market.pause()

            def pump(self):
                return market.pump(1)

            def finish(self, status, reason, time_ns):
                market.pause()

        kernel = ExperimentSession(
            Driver(),
            market.reader,
            clock=clock,
            start_ns=start,
            end_ns=end,
            sink=messages.append,
            operation=operation,
        )
        self.addCleanup(kernel.close)
        return market, clock, kernel, messages

    def test_receipt_prefix_does_not_move_to_service_time(self):
        observed = []
        market, clock, kernel, messages = self.kernel(
            lambda request, position: observed.append((dict(position), market.snapshot.sequence))
            or {'ok': True}
        )
        kernel.play()
        clock.stamp = 10
        result = kernel.submit({'operation': 'test'})
        clock.stamp = 31
        kernel.tick()
        self.assertEqual(observed[0][0]['time_ns'], '10')
        self.assertEqual(observed[0][1], 4)
        self.assertEqual(result.result(2)['receipt_target_ns'], '10')
        self.assertEqual(market.snapshot.sequence, 6)

    def test_pause_seals_same_tick_commands_and_rejects_new_operations(self):
        calls = []
        market, clock, kernel, _ = self.kernel(
            lambda request, position: calls.append(position) or {'ok': True}
        )
        kernel.play()
        clock.stamp = 20
        future = kernel.submit({'action': 'one'})
        paused = kernel.control('pause')
        kernel.tick()
        self.assertEqual(paused.result(1)['state'], 'paused')
        self.assertEqual(len(calls), 1)
        self.assertEqual(future.result(2)['position']['time_ns'], '20')
        self.assertFalse(clock.running)
        with self.assertRaises(ContractError):
            kernel.submit({'action': 'two'})
        resumed = kernel.control('resume')
        kernel.tick()
        self.assertTrue(resumed.result(1)['applied'])
        clock.stamp = 21
        next_result = kernel.submit({'action': 'two'})
        clock.stamp = 22
        kernel.tick()
        self.assertTrue(next_result.result(2)['result']['ok'])

    def test_stop_invalidates_unstarted_without_catching_up_to_source(self):
        calls = []
        market, clock, kernel, _ = self.kernel(
            lambda request, position: calls.append(request) or {}
        )
        kernel.play()
        clock.stamp = 20
        future = kernel.submit({'action': 'one'})
        kernel.control('stop')
        kernel.tick()
        self.assertEqual(market.snapshot.sequence, 3)
        self.assertEqual(calls, [])
        with self.assertRaises(ContractError):
            future.result(2)
        self.assertEqual(kernel.snapshot()['state'], 'stopped')

    def test_end_preserves_allowed_events_but_invalidates_pending_command(self):
        calls = []
        market, clock, kernel, _ = self.kernel(
            lambda request, position: calls.append(request) or {}, end=31
        )
        kernel.play()
        clock.stamp = 30
        future = kernel.submit({'action': 'late'})
        clock.stamp = 31
        kernel.tick()
        self.assertEqual(market.snapshot.sequence, 6)
        self.assertEqual(calls, [])
        with self.assertRaises(ContractError):
            future.result(2)
        self.assertEqual(kernel.snapshot()['state'], 'completed')

    def test_notice_failure_after_admission_cancels_once_and_releases_capacity(self):
        _, clock, kernel, _ = self.kernel(lambda request, position: self.fail('must not execute'))
        kernel.play()
        clock.stamp = 10
        with patch.object(kernel, 'publish_notice', side_effect=ContractError('notification full')):
            with self.assertRaisesRegex(ContractError, 'notification full'):
                kernel.submit({'operation': 'not started'})
        kernel.tick()
        self.assertEqual(kernel.snapshot()['state'], 'failed')
        self.assertEqual(kernel._pending, {})
        self.assertEqual(kernel.channels.ended.result(2)['status'], 'failed')

    def test_query_cancellation_failure_still_seals_terminal_output(self):
        _, _, kernel, _ = self.kernel(lambda request, position: {})
        kernel.play()

        def broken():
            raise OSError('query cleanup failed')

        kernel._cancel_queries = broken
        kernel.control('stop')
        kernel.tick()
        end = kernel.channels.ended.result(2)
        self.assertEqual(end['status'], 'failed')
        self.assertIn('query cleanup failed', end['reason'])

    def test_failed_read_capture_does_not_leave_a_pending_request(self):
        _, clock, kernel, _ = self.kernel(lambda request, position: {})
        kernel.bind_market_reads(lambda request, position, receipt: dict(captured=position))
        kernel.play()
        clock.stamp = 10
        with patch.object(kernel.reader, 'prefix', side_effect=OSError('index unavailable')):
            with self.assertRaisesRegex(OSError, 'index unavailable'):
                kernel.submit_read({'tool': 'market.quote'})
        self.assertEqual(kernel._pending, {})
        # A failed capture must release capacity exactly once and permit a later read.
        result = kernel.submit_read({'tool': 'market.quote'}).result(2)
        self.assertEqual(result['result']['captured']['time_ns'], '10')
        kernel.control('stop')
        kernel.tick()
        self.assertEqual(kernel.channels.ended.result(2)['status'], 'stopped')

    def test_read_position_keeps_news_boundary_separate_from_financial_sequence(self):
        from sim_platform.market.models import NewsRecord
        from sim_platform.market.prepared.publications import PublicationReader

        _, clock, kernel, _ = self.kernel(lambda request, position: {})
        reader = PublicationReader(kernel.reader, (NewsRecord('news', 10, 'p', 'headline'),))
        kernel.reader = reader
        kernel.bind_market_reads(lambda request, position, receipt: dict(captured=position))
        kernel.play()
        clock.stamp = 10
        result = kernel.submit_read({'tool': 'market.quote'}).result(2)
        self.assertEqual(result['result']['captured']['sequence'], '4')
        self.assertEqual(result['result']['captured']['boundary'], '5')


class PlatformPreparedBindingTests(unittest.TestCase):
    setUp = PreparedMarketFixture.setUp

    def test_both_public_entries_share_preparation_and_original_financial_path(self):
        from sim_platform import Platform

        with Platform(self.root / 'app') as app:
            profile = app.create_profile('prepared')
            one = app.create_account(profile, 'one', 10000)
            two = app.create_account(profile, 'two', 10000)
            direct = app.open_experiment(
                profile,
                one,
                source=self.source,
                method='human',
                settings={'start_mode': 'first_book'},
                execution_mode='controlled',
            )
            staged = app.open_research(method='human')
            staged.begin_prepare(self.source, start_mode='first_book')
            while staged.preparation['status'] == 'preparing':
                staged.prepare_tick()
            staged.start(profile, two, execution_mode='controlled')
            self.assertEqual(direct.market.snapshot, staged.market.snapshot)
            self.assertEqual(
                direct.source['prepared']['artifact'], staged.source['prepared']['artifact']
            )
            self.assertNotEqual(direct.run_id, staged.run_id)
            self.assertNotEqual(
                direct.runner.snapshot.account.account_id, staged.runner.snapshot.account.account_id
            )
            direct.step()
            self.assertNotEqual(direct.market.snapshot.sequence, staged.market.snapshot.sequence)
            direct.close()
            staged.close()

    def test_live_policy_uses_shared_prepared_execution_owner_without_ui_ticks(self):
        from sim_platform import Platform

        with Platform(self.root / 'live-app') as app:
            profile = app.create_profile('live')
            account = app.create_account(profile, 'cash', 10000)
            run = app.open_experiment(
                profile, account, source=self.source, method='human', execution_mode='live'
            )
            from sim_platform.experiments.execution.session import ExperimentSession

            self.assertIsInstance(run.runner.pipeline, ExperimentSession)
            run.play()
            result = run.runner.act(
                [
                    dict(
                        kind='submit',
                        intent=dict(
                            client_id='one', symbol='AAPL', side='buy', quantity=1, limit_price=1
                        ),
                    )
                ]
            )
            self.assertEqual(result['results'][0]['status'], 'accepted')
            run.pause()
            frozen = run.runner.pipeline.clock.target_ns()
            time.sleep(0.01)
            self.assertEqual(run.runner.pipeline.clock.target_ns(), frozen)
            run.play()
            run.stop()
            self.assertEqual(app.snapshot(profile, account).orders[0].status, 'cancelled')

    def test_live_agent_uses_same_kernel_and_starts_runtime_work(self):
        from sim_platform import Platform
        from support.agents import definition, settings

        with Platform(self.root / 'agent-app') as app:
            profile = app.create_profile('agent')
            account = app.create_account(profile, 'cash', 10000)
            app.create_agent(profile, definition())
            config = dict(settings(), start_ns='3', end_ns='50000000000')
            run = app.open_experiment(
                profile,
                account,
                source=self.source,
                method='agent',
                agent_id='demo',
                revision=1,
                settings=config,
                execution_mode='live',
            )
            from sim_platform.experiments.execution.session import ExperimentSession

            self.assertIsInstance(run.runner.pipeline, ExperimentSession)
            run.play()
            work = run.runner.next_work().result(2)['result']
            self.assertIn('work_id', work)
            run.pause()
            run.play()
            run.stop()
            self.assertTrue(run.runner.closed)

    def test_news_overlay_preserves_equal_time_order_without_incrementing_source_sequence(self):
        from sim_platform import Platform
        import json

        news = self.root / 'news.json'
        news.write_text(
            json.dumps(
                [
                    dict(
                        record_id='n1',
                        available_time_ns=10,
                        period='p',
                        headline='one',
                        provenance='fixture',
                        revision=1,
                    )
                ]
            )
        )
        with Platform(self.root / 'news-app') as app:
            profile = app.create_profile('news')
            account = app.create_account(profile, 'cash', 10000)
            run = app.open_experiment(
                profile,
                account,
                source=self.source,
                method='human',
                settings={'news': str(news)},
                execution_mode='controlled',
            )
            self.assertEqual(run.market.snapshot.sequence, 3)
            run.step()
            self.assertEqual(run.market.snapshot.sequence, 3)
            self.assertEqual(run.market.news()[0].record_id, 'n1')
            self.assertEqual(run.market.trades(), ())
            run.step()
            self.assertEqual(run.market.snapshot.sequence, 4)
            self.assertEqual(run.market.trades()[0].match_id, 1)
            run.close()

    def test_market_query_returns_while_financial_owner_is_deliberately_blocked(self):
        from threading import Event
        from sim_platform import Platform
        from support.agents import definition, settings
        from support.tools import request

        with Platform(self.root / 'read-app') as app:
            profile = app.create_profile('reads')
            account = app.create_account(profile, 'cash', 10000)
            app.create_agent(profile, definition())
            config = dict(settings(), start_ns='3', end_ns='50000000000')
            run = app.open_experiment(
                profile,
                account,
                source=self.source,
                method='agent',
                agent_id='demo',
                revision=1,
                settings=config,
                execution_mode='live',
            )
            run.play()
            work = run.runner.next_work().result(2)['result']
            pipeline = run.runner.pipeline
            entered, release = Event(), Event()
            original = pipeline.operation

            def hold(payload, position):
                entered.set()
                if not release.wait(3):
                    raise RuntimeError('test owner release missing')
                return {'ok': True}

            pipeline.operation = hold
            pending = pipeline.submit({'test': 'hold'})
            self.assertTrue(entered.wait(1))
            try:
                value = request('market.quote', {'symbol': 'AAPL'}, run=run.run_id)
                value.update(work_id=work['work_id'], epoch=work['epoch'])
                response = run.request_tool(value).result(2)['result']
                self.assertEqual(response['status'], 'ok')
                self.assertIsNone(response['cutoff']['account_sequence'])
                self.assertIsNone(response['cutoff']['account_sequence'])
                self.assertFalse(pending.done())
            finally:
                release.set()
                pending.result(2)
                pipeline.operation = original
                run.stop()


class AgentLocalModelTests(AgentModelFixture, unittest.TestCase):

    def test_model_io_and_payload_are_in_agent_without_app_model_thread(self):
        import os
        import threading

        app, experiment, incoming = self.open_model(ChildModelProvider())
        self.assertFalse(any(t.name == 'model-async' for t in threading.enumerate()))
        experiment.play()
        self.assertTrue(incoming.poll(3))
        reply = incoming.recv()
        self.assertIsNone(reply['error'])
        self.assertNotEqual(reply['result']['pid'], os.getpid())
        self.assertEqual(reply['result']['pid'], experiment.runner.process_host.snapshot()['pid'])
        self.assertTrue(reply['result']['credential_selected'])
        self.assertEqual(reply['result']['calls'], 1)
        experiment.stop()
        for path in self.root.rglob('*.json*'):
            self.assertNotIn('fixture-secret', path.read_text())

    def test_on_time_raw_receipt_with_slow_agent_parse_is_not_timeout(self):
        _, experiment, incoming = self.open_model(
            ChildModelProvider(delay=0.005, parse_delay=0.15), timeout='0.05'
        )
        experiment.play()
        self.assertTrue(incoming.poll(3))
        reply = incoming.recv()
        self.assertIsNone(reply['error'])
        self.assertEqual(reply['result']['calls'], 1)
        experiment.stop()

    def test_runtime_authorizes_retry_and_preserves_provider_error_classification(self):
        _, experiment, incoming = self.open_model(
            ChildModelProvider(errors=['RATE_LIMIT', None]), timeout='0.5'
        )
        experiment.play()
        self.assertTrue(incoming.poll(4))
        reply = incoming.recv()
        self.assertIsNone(reply['error'])
        self.assertEqual(reply['result']['calls'], 2)
        experiment.stop()

    def test_fatal_auth_error_is_not_retried(self):
        _, experiment, incoming = self.open_model(
            ChildModelProvider(errors=['AUTH_FAILED']), timeout='0.5'
        )
        experiment.play()
        self.assertTrue(incoming.poll(3))
        reply = incoming.recv()
        self.assertEqual(reply['error'], 'AUTH_FAILED')
        experiment.stop()

    def test_pause_preserves_on_time_reply_and_freezes_active_deadline(self):
        _, experiment, incoming = self.open_model(ChildModelProvider(delay=0.15), timeout='0.3')
        experiment.play()
        deadline = time.monotonic() + 2
        while experiment.runner._tools.controls.runtime.snapshot()['attempt']['state'] != 'running':
            if time.monotonic() > deadline:
                self.fail('attempt did not start')
            time.sleep(0.005)
        experiment.pause()
        frozen = experiment.runner.pipeline.clock.target_ns()
        time.sleep(0.4)
        self.assertEqual(experiment.runner.pipeline.clock.target_ns(), frozen)
        self.assertFalse(incoming.poll())
        experiment.play()
        self.assertTrue(incoming.poll(2))
        self.assertIsNone(incoming.recv()['error'])
        experiment.stop()

    def test_stop_cancels_agent_local_network_without_waiting_for_provider(self):
        _, experiment, incoming = self.open_model(ChildModelProvider(delay=20), timeout='30')
        experiment.play()
        deadline = time.monotonic() + 2
        while experiment.runner._tools.controls.runtime.snapshot()['attempt']['state'] != 'running':
            if time.monotonic() > deadline:
                self.fail('attempt did not start')
            time.sleep(0.005)
        begun = time.monotonic()
        experiment.stop()
        self.assertLess(time.monotonic() - begun, 3)
        self.assertFalse(experiment.runner.process_host.snapshot()['state'] == 'running')


class PreparedOperationReplayTests(unittest.TestCase):
    setUp = PreparedMarketFixture.setUp

    def test_rule_replay_uses_recorded_actions_without_reinvoking_policy(self):
        from sim_platform import Platform

        self.source.write_bytes(
            directory('AAPL', timestamp=1)
            + add(1, 'B', 100, 99, 'AAPL', timestamp=2)
            + add(2, 'S', 100, 101, 'AAPL', timestamp=3)
            + b''.join(
                add(index, 'S', 20, 101, 'AAPL', timestamp=stamp)
                # Live time is real time: keep liquidity coming for 20 s, so a rule whose
                # process starts late on a loaded machine still finds shares to buy.
                for index, stamp in enumerate(
                    [400_000_000, 800_000_000, 1_200_000_000]
                    + [second * 1_000_000_000 for second in range(2, 21)],
                    4,
                )
            )
            + b'\0\0'
        )
        with Platform(self.root / 'rule-replay') as app:
            profile = app.create_profile('rule replay')
            account = app.create_account(profile, 'original', 10000)
            target = app.create_account(profile, 'replayed', 10000)
            run = app.open_experiment(
                profile,
                account,
                source=self.source,
                method='rule',
                settings=dict(quantity=1),
                execution_mode='live',
            )
            # One slow first decision must not fail the run; only sustained lag does.
            self.assertEqual(run.runner.pipeline.lag_window_ns, 1_000_000_000)
            run.play()
            # Correctness, not speed: a loaded CI runner under coverage can be slow.
            deadline = time.monotonic() + 30
            while not any(o.filled for o in app.snapshot(profile, account).orders):
                if time.monotonic() > deadline:
                    # Intermittent on CI only: report what the run did to find the cause.
                    events = app.run_events(profile, run.run_id)
                    self.fail(
                        'Rule original fill missing: '
                        + repr(
                            dict(
                                execution=run.view().get('execution'),
                                events=[(e['kind'], e['time_ns']) for e in events][-15:],
                                orders=app.snapshot(profile, account).orders,
                            )
                        )[:3000]
                    )
                time.sleep(0.005)
            path = run.runner.recorder.path
            run.stop()
            with patch(
                'sim_platform.decisions.policy.policies.BuyHoldSell.decide',
                side_effect=AssertionError('Replay cannot invoke Rule'),
            ):
                result = app.replay_prepared_operations(profile, target, path)
            self.assertTrue(result['matched'])

    def test_agent_submit_fill_and_cancel_replay_without_starting_agent(self):
        from sim_platform import Platform
        from support.agents import definition, settings
        from support.tools import request

        self.source.write_bytes(
            directory('AAPL', timestamp=1)
            + add(1, 'B', 100, 99, 'AAPL', timestamp=2)
            + add(2, 'S', 100, 101, 'AAPL', timestamp=3)
            + add(3, 'S', 20, 101, 'AAPL', timestamp=150_000_000)
            + add(4, 'S', 20, 102, 'AAPL', timestamp=800_000_000)
            + b'\0\0'
        )
        with Platform(self.root / 'agent-replay') as app:
            profile = app.create_profile('agent replay')
            account = app.create_account(profile, 'original', 10000)
            target = app.create_account(profile, 'replayed', 10000)
            app.create_agent(profile, definition())
            config = dict(
                settings(),
                start_ns='3',
                end_ns='800000000',
                granted_tools=['market.quote', 'orders.submit_limit', 'orders.cancel'],
            )
            run = app.open_experiment(
                profile,
                account,
                source=self.source,
                method='agent',
                agent_id='demo',
                revision=1,
                settings=config,
                execution_mode='live',
            )
            run.play()
            work = run.runner.next_work().result(2)['result']

            def call(tool, args, identity):
                value = request(tool, args, number=identity, run=run.run_id)
                value.update(work_id=work['work_id'], epoch=work['epoch'])
                return run.request_tool(value).result(2)['result']

            call(
                'orders.submit_limit',
                dict(symbol='AAPL', side='buy', quantity_shares=5, limit_price_usd='0.0101'),
                'cross',
            )
            resting = call(
                'orders.submit_limit',
                dict(symbol='AAPL', side='buy', quantity_shares=1, limit_price_usd='0.0001'),
                'rest',
            )
            call('orders.cancel', dict(order_id=resting['data']['order']['order_id']), 'cancel')
            # Terminal/unknown cancellations are tool outcomes without a new trade.
            call('orders.cancel', dict(order_id=resting['data']['order']['order_id']), 'again')
            unknown = call('orders.cancel', dict(order_id='missing-order'), 'unknown')
            self.assertEqual(unknown['status'], 'error')
            deadline = time.monotonic() + 2
            while not any(o.filled == 5 for o in app.snapshot(profile, account).orders):
                if time.monotonic() > deadline:
                    self.fail('Agent original fill missing')
                time.sleep(0.005)
            path = run.runner._recorder.path
            run.stop()
            with patch(
                'sim_platform.agents.hosting.host.AgentProcessHost',
                side_effect=AssertionError('Replay cannot start Agent'),
            ):
                result = app.replay_prepared_operations(profile, target, path)
            self.assertTrue(result['matched'])
            self.assertEqual(result['financial']['cash'], 9495)

    def test_replay_uses_original_effective_operations_and_never_invokes_a_model(self):
        from sim_platform import Platform

        self.source.write_bytes(
            directory('AAPL', timestamp=1)
            + add(1, 'B', 100, 99, 'AAPL', timestamp=2)
            + add(2, 'S', 100, 101, 'AAPL', timestamp=3)
            + add(3, 'S', 20, 101, 'AAPL', timestamp=100_000_000)
            + add(4, 'S', 20, 102, 'AAPL', timestamp=500_000_000)
            + b'\0\0'
        )
        with Platform(self.root / 'replay-app') as app:
            profile = app.create_profile('replay')
            account = app.create_account(profile, 'original', 10000)
            target = app.create_account(profile, 'replayed', 10000)
            experiment = app.open_experiment(
                profile, account, source=self.source, execution_mode='live'
            )
            experiment.play()
            experiment.runner.act(
                [
                    dict(
                        kind='submit',
                        intent=dict(
                            client_id='buy', symbol='AAPL', side='buy', quantity=5, limit_price=101
                        ),
                    )
                ]
            )
            deadline = time.monotonic() + 2
            while experiment.runner.core.snapshot.account.orders[0].filled < 5:
                if time.monotonic() > deadline:
                    self.fail('expected source fill did not arrive')
                time.sleep(0.005)
            path = experiment.runner.recorder.path
            experiment.stop()
            with patch(
                'sim_platform.models.providers.openai.OpenAIResponses.request',
                side_effect=AssertionError('Replay cannot invoke model'),
            ):
                result = app.replay_prepared_operations(profile, target, path)
            self.assertTrue(result['matched'])
            self.assertEqual(result['model_calls'], 0)
            self.assertEqual(result['financial']['cash'], 9495)
            self.assertEqual(result['financial']['orders'][0][-2:], [5, 'filled'])

    def test_old_or_unsealed_logs_are_explicitly_rejected(self):
        from sim_platform.application.experiments.replay import decode_plan

        for value in (
            {'header': {}, 'records': [], 'end': None},
            {
                'header': {'version': 'experiment-operations/v2'},
                'records': [],
                'end': {'status': 'stopped'},
            },
        ):
            with self.assertRaises(ContractError):
                decode_plan(value)


class PreparedIdleSpanTests(unittest.TestCase):
    setUp = PreparedMarketFixture.setUp

    def test_certified_batch_matches_every_financial_counter_and_consumed_liquidity(self):
        from dataclasses import asdict
        from sim_platform import Platform
        from sim_platform.domain.json_values import copy_json
        from sim_platform.application.experiments.replay import financial_values
        from itch_fixtures import reduce

        self.source.write_bytes(
            directory('AAPL', timestamp=1)
            + add(1, 'B', 100, 99, 'AAPL', timestamp=2)
            + add(2, 'S', 100, 101, 'AAPL', timestamp=3)
            + b''.join(add(i, 'B', 10, 95 - i, 'AAPL', timestamp=i) for i in range(4, 64))
            + reduce('E', 2, 1, timestamp=64)
            + frame(common('B', timestamp=65) + struct.pack('>Q', 123))
            + b'\0\0'
        )
        outcomes = []
        batched = 0
        with Platform(self.root / 'span-app') as app:
            profile = app.create_profile('spans')
            for optimized in (False, True):
                account = app.create_account(profile, str(optimized), 100000)
                run = app.open_experiment(
                    profile, account, source=self.source, execution_mode='controlled'
                )
                run.runner.act(
                    [
                        dict(
                            kind='submit',
                            intent=dict(
                                client_id='cross',
                                symbol='AAPL',
                                side='buy',
                                quantity=5,
                                limit_price=101,
                            ),
                        ),
                        dict(
                            kind='submit',
                            intent=dict(
                                client_id='passive',
                                symbol='AAPL',
                                side='buy',
                                quantity=1,
                                limit_price=1,
                            ),
                        ),
                    ]
                )
                run.runner.play()
                execution = run.runner._execution
                while run.market.next_event_time_ns is not None:
                    end = run.market.reader.idle_span_end(run.market.merged_prefix)
                    count = 0
                    if optimized and end - run.market.merged_prefix > 1:
                        count, _ = execution.advance_idle_prefix(end)
                        batched += count
                    if not count:
                        run.runner.pump(1)
                state = execution.replay_state
                outcomes.append(
                    (
                        financial_values(asdict(execution.snapshot)),
                        copy_json(
                            {
                                key: state[key]
                                for key in (
                                    'tick',
                                    'boundary',
                                    'market_sequence',
                                    'bids',
                                    'asks',
                                    'market',
                                )
                            }
                        ),
                    )
                )
                run.close()
        self.assertGreater(batched, 40)
        self.assertEqual(outcomes[0], outcomes[1])
        self.assertTrue(any(order[-2:] == [5, 'filled'] for order in outcomes[1][0]['orders']))

    def test_crossable_order_with_future_eligibility_cannot_be_batched_past_latency(self):
        from sim_platform import Platform
        from sim_platform.execution.models import ExecutionConfig

        self.source.write_bytes(
            directory('AAPL', timestamp=1)
            + add(1, 'B', 100, 99, 'AAPL', timestamp=2)
            + add(2, 'S', 100, 101, 'AAPL', timestamp=3)
            + b''.join(add(i, 'B', 10, 95 - i, 'AAPL', timestamp=i) for i in range(4, 40))
            + b'\0\0'
        )
        with Platform(self.root / 'span-latency') as app:
            profile = app.create_profile('latency')
            account = app.create_account(profile, 'cash', 100000)
            run = app.open_experiment(
                profile,
                account,
                source=self.source,
                execution_mode='controlled',
                execution_config=ExecutionConfig(latency_ns=20),
            )
            run.runner.act(
                [
                    dict(
                        kind='submit',
                        intent=dict(
                            client_id='cross',
                            symbol='AAPL',
                            side='buy',
                            quantity=5,
                            limit_price=101,
                        ),
                    )
                ]
            )
            run.runner.play()
            while run.market.time_ns < 10:
                run.runner.pump(1)
            end = run.market.reader.idle_span_end(run.market.merged_prefix)
            self.assertEqual(run.runner._execution.advance_idle_prefix(end)[0], 0)
            while run.market.time_ns < 22:
                run.runner.pump(1)
            self.assertEqual(run.runner.snapshot.account.orders[0].filled, 0)
            run.runner.pump(1)
            self.assertEqual(run.market.time_ns, 23)
            self.assertEqual(run.runner.snapshot.account.orders[0].filled, 5)
            run.close()
