"""Original actions replay without scheduling controls, policies or model calls."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import asdict
from unittest.mock import patch
import sys
import time
import unittest

from sim_platform import Platform, ContractError
from sim_platform.execution import ExecutionConfig
from support.clock import FakeTime, TestClock
from sim_platform.persistence.agent_log_files import read_agent_log
from sim_platform.application.experiments.replay import replay
from sim_platform.replay.plan import decode_plan
from support import prepared_market as prepared
from support.prepared_market import directory, add


class OriginalOperationsTests(unittest.TestCase):
    setUp = prepared.PreparedMarketFixture.setUp

    def open_with_clock(self, account, clock, **options):
        # Human clock construction is internal; inject only that factory, not Replay.
        with patch(
            'sim_platform.application.experiments.factory.SharedExperimentClock', return_value=clock
        ):
            return self.app.open_experiment(
                self.profile, account, source=self.source, execution_mode='live', **options
            )

    def act(self, experiment, clock, actions):
        # Closing the current admission timestamp requires a later clock boundary.
        # Advance nanoseconds while waiting, without racing the next market event.
        with ThreadPoolExecutor(max_workers=1) as worker:
            result = worker.submit(experiment.runner.act, actions)
            while not result.done():
                clock.advance_to(clock.target_ns() + 1)
                time.sleep(0.002)
            return result.result()

    def original(self, *, trade=True, fail=False, eof=False):
        self.source.write_bytes(
            directory('AAPL', timestamp=1)
            + add(1, 'B', 20, 99, 'AAPL', timestamp=2)
            + add(2, 'S', 2, 101, 'AAPL', timestamp=3)
            + add(3, 'B', 10, 98, 'AAPL', timestamp=150_000_000)
            + add(4, 'S', 10, 102, 'AAPL', timestamp=300_000_000)
            + b'\0\0'
        )
        self.app = Platform(self.root / 'app')
        self.addCleanup(self.app.close)
        self.profile = self.app.create_profile('Replay')
        self.account = self.app.create_account(self.profile, 'original', 10000)
        clock = TestClock(3, FakeTime())
        experiment = self.open_with_clock(
            self.account, clock, execution_config=ExecutionConfig(fee_per_share=1)
        )
        self.addCleanup(experiment.close)
        experiment.play()
        if trade:
            self.act(
                experiment,
                clock,
                [
                    dict(
                        kind='submit',
                        intent=dict(
                            client_id='buy', symbol='AAPL', side='buy', quantity=5, limit_price=101
                        ),
                    )
                ],
            )
            clock.advance_to(150_000_001)
            deadline = time.monotonic() + 3
            while not any(
                o.filled == 2 for o in self.app.snapshot(self.profile, self.account).orders
            ):
                if time.monotonic() > deadline:
                    self.fail('partial fill did not arrive')
                time.sleep(0.002)
            experiment.pause()
            experiment.play()
            self.act(experiment, clock, [dict(kind='cancel', client_id='buy')])
            # A rejected request is an actual outcome too.
            self.act(
                experiment,
                clock,
                [
                    dict(
                        kind='submit',
                        intent=dict(
                            client_id='overdraft',
                            symbol='AAPL',
                            side='buy',
                            quantity=1000,
                            limit_price=101,
                        ),
                    )
                ],
            )
        if fail:
            experiment.runner.pipeline.sequencer.stop('failed')
            experiment.runner.pipeline.wake()
            experiment.runner.pipeline.channels.ended.result(3)
        elif eof:
            clock.advance_to(300_000_001)
            experiment.runner.pipeline.channels.ended.result(3)
        else:
            experiment.stop()
        self.archive = read_agent_log(experiment.runner.recorder.path)
        return experiment.run_id

    def playback(self, archive=None):
        target = self.app.create_account(self.profile, 'destination', 10000)
        # A replay can use the financial engine but must not launch any carrier,
        # scheduler, model, or reapply the original pause/resume controls.
        with (
            patch(
                'sim_platform.agents.hosting.host.AgentProcessHost',
                side_effect=AssertionError('agent'),
            ),
            patch(
                'sim_platform.models.providers.openai.OpenAIResponses.request',
                side_effect=AssertionError('model'),
            ),
            patch(
                'sim_platform.experiments.execution.session.ExperimentSession',
                side_effect=AssertionError('runtime'),
            ),
            patch(
                'sim_platform.decisions.policy.runner.PolicyDecisionRunner.pause',
                side_effect=AssertionError('pause'),
            ),
        ):
            return replay(
                self.app._operations,
                self.app._new_runner,
                self.profile,
                target,
                self.archive if archive is None else archive,
            )

    def test_partial_cancel_rejection_fees_and_original_data_remain_unchanged(self):
        run_id = self.original()
        before = self.app.run_events(self.profile, run_id)
        snapshot = asdict(self.app.snapshot(self.profile, self.account))
        result = self.playback()
        self.assertTrue(result['matched'])
        self.assertTrue(result['events_verified'])
        self.assertEqual(result['model_calls'], 0)
        self.assertEqual(result['commands'], 3)
        kinds = [event['kind'] for event in result['events']]
        self.assertEqual(kinds, ['accepted', 'fill', 'cancelled', 'rejected'])
        self.assertEqual(result['financial']['fees'], 2)
        self.assertEqual(result['financial']['orders'][0][-2:], [2, 'cancelled'])
        self.assertEqual(self.app.run_events(self.profile, run_id), before)
        self.assertEqual(asdict(self.app.snapshot(self.profile, self.account)), snapshot)

    def test_no_trade_and_natural_end(self):
        self.original(trade=False, eof=True)
        result = self.playback()
        self.assertEqual(result['commands'], 0)
        self.assertEqual(result['events'], [])
        self.assertEqual(result['financial']['cash'], 10000)

    def test_failed_experiment_with_sealed_financial_endpoint(self):
        self.original(trade=True, fail=True)
        self.assertEqual(self.archive['end']['status'], 'failed')
        self.assertTrue(self.playback()['matched'])

    def test_controls_and_model_attempt_audit_are_not_reexecuted(self):
        self.original(trade=False)
        archive = deepcopy(self.archive)
        audit = [
            dict(index=0, kind='control', event=event, details={})
            for event in ('pause', 'resume', 'model_timeout', 'model_retry')
        ]
        archive['records'][1:1] = audit
        for index, record in enumerate(archive['records'], 1):
            record['index'] = index
        archive['end']['records'] = len(archive['records'])
        self.assertTrue(self.playback(archive)['matched'])

    def test_missing_duplicate_or_position_mismatched_commands_are_rejected(self):
        self.original()
        for change in ('missing', 'duplicate', 'position', 'footer', 'gap', 'unknown', 'shape'):
            with self.subTest(change=change):
                archive = deepcopy(self.archive)
                processed = next(
                    r for r in archive['records'] if r.get('event') == 'command_processed'
                )
                if change == 'shape':
                    archive['end'] = 'invalid footer'
                elif change == 'missing':
                    archive['records'].remove(processed)
                elif change == 'duplicate':
                    archive['records'].insert(
                        archive['records'].index(processed), deepcopy(processed)
                    )
                elif change == 'position':
                    processed['details']['effective_ns'] = '0'
                elif change == 'footer':
                    archive['end']['records'] = 999
                elif change == 'gap':
                    archive['records'][0]['index'] = 999
                else:
                    admitted = next(
                        r for r in archive['records'] if r.get('event') == 'command_admitted'
                    )
                    admitted['details']['payload']['operation'] = 'unknown'
                if change not in ('gap', 'footer', 'shape'):
                    for index, record in enumerate(archive['records'], 1):
                        record['index'] = index
                    archive['end']['records'] = len(archive['records'])
                with self.assertRaises(ContractError):
                    decode_plan(archive)

    def test_damaged_event_or_account_or_artifact_is_rejected(self):
        self.original()
        final = next(
            r['details'] for r in self.archive['records'] if r.get('event') == 'prepared_final'
        )
        final['events'][1]['data']['price'] += 1
        with self.assertRaisesRegex(ContractError, 'events mismatch'):
            self.playback()
        final['events'][1]['data']['price'] -= 1
        final['financial']['account']['cash'] += 1
        with self.assertRaisesRegex(ContractError, 'financial result mismatch'):
            self.playback()
        final['financial']['account']['cash'] -= 1
        artifact = next(
            r['details']['artifact']
            for r in self.archive['records']
            if r.get('event') == 'prepared_run'
        )
        from pathlib import Path

        with (Path(artifact) / 'events.bin').open('ab') as stream:
            stream.write(b'x')
        with self.assertRaisesRegex(ContractError, 'length mismatch'):
            self.playback()

    def test_existing_holdings_and_prior_fees_are_preserved(self):
        self.original()
        target = self.app.create_account(self.profile, 'Matching portfolio', 10000)
        from sim_platform.persistence.agent_log_files import AgentLogFile

        # Replay the first run to obtain an independently computed equal portfolio.
        first_log = self.root / 'first.jsonl'
        adapter = AgentLogFile(first_log)
        adapter.append(dict(header=self.archive['header']))
        for record in self.archive['records']:
            adapter.append(dict(record=record))
        adapter.append(dict(end=self.archive['end']))
        adapter.close()
        self.app.replay_prepared_operations(self.profile, target, first_log)
        clock = TestClock(3, FakeTime())
        second = self.open_with_clock(self.account, clock)
        self.addCleanup(second.close)
        second.play()
        self.act(
            second,
            clock,
            [
                dict(
                    kind='submit',
                    intent=dict(
                        client_id='sell', symbol='AAPL', side='sell', quantity=2, limit_price=99
                    ),
                )
            ],
        )
        clock.advance_to(150_000_001)
        deadline = time.monotonic() + 3
        while not any(
            o.intent_id.endswith(':intent:sell') and o.filled == 2
            for o in self.app.snapshot(self.profile, self.account).orders
        ):
            if time.monotonic() > deadline:
                self.fail('sell did not fill')
            time.sleep(0.002)
        second.stop()
        result = self.app.replay_prepared_operations(
            self.profile, target, second.runner.recorder.path
        )
        self.assertTrue(result['events_verified'])
        self.assertEqual(result['financial']['positions'], [])
        self.assertEqual(result['financial']['fees'], 2)

    def test_unexecuted_admission_is_not_a_trade(self):
        self.original(trade=False)
        archive = deepcopy(self.archive)
        seed = next(r['details'] for r in archive['records'] if r.get('event') == 'prepared_run')
        # A queued command was cancelled before reaching the financial handler.
        records = [
            dict(
                kind='notice',
                event='command_admitted',
                details=dict(
                    command_id='1',
                    admission_sequence='1',
                    effective_ns=str(seed['start_ns']),
                    prepared_prefix='3',
                    source_sequence='3',
                    payload=dict(operation='manual'),
                ),
            ),
            dict(kind='notice', event='command_cancelled', details=dict(command_id='1')),
        ]
        index = next(
            i for i, r in enumerate(archive['records']) if r.get('event') == 'prepared_final'
        )
        archive['records'][index:index] = records
        for index, record in enumerate(archive['records'], 1):
            record['index'] = index
        archive['end']['records'] = len(archive['records'])
        self.assertEqual(self.playback(archive)['commands'], 0)

    def test_previous_log_without_event_evidence_reports_limited_comparison(self):
        self.original(trade=False)
        final = next(
            r['details'] for r in self.archive['records'] if r.get('event') == 'prepared_final'
        )
        del final['events']
        self.assertFalse(self.playback()['events_verified'])


class BuiltInAgentReplayTests(unittest.TestCase):
    setUp = prepared.PreparedMarketFixture.setUp

    @unittest.skipIf(
        sys.platform == 'win32',
        'known open item: on Windows CI the scripted model fixture never delivers this order',
    )
    def test_template_retry_then_trade_replays_without_model_or_agent(self):
        from support import carrier as template
        from support.clock import until

        experiment = template.AgentExperimentFixture.open(
            self,
            [
                'RATE_LIMIT',
                dict(
                    output=[
                        template.call(
                            'orders.submit_limit',
                            1,
                            dict(
                                symbol='AAPL',
                                side='buy',
                                quantity_shares=1,
                                limit_price_usd='0.0001',
                            ),
                        )
                    ]
                ),
                dict(output=[template.call('control.finish', 2)]),
            ],
        )
        self.addCleanup(experiment.close)
        experiment.play()
        # Starting the Agent process takes longer on Windows runners.
        until(lambda: len(self.app.snapshot(self.profile, self.account).orders) == 1, 20)
        experiment.stop()
        target = self.app.create_account(self.profile, 'replayed', 10000000)
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
        self.assertTrue(result['events_verified'])
        self.assertEqual([e['kind'] for e in result['events']], ['accepted', 'cancelled'])
