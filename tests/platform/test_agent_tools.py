"""Hand-calculated reads and actual ResearchController/pipeline integration."""

from dataclasses import replace
import struct
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch

from sim_platform import Platform, ContractError, Intent
from sim_platform.agents.tools.protocol.contracts import READ_TOOLS, ToolError, validate_request
from sim_platform.agents.tools.reads.capture import capture_read
from sim_platform.agents.tools.protocol.responses import error_response
from support.clock import TestClock as ExperimentClock
from sim_platform.decisions.agent.runner import AgentDecisionRunner
from sim_platform.decisions import (
    DecisionRunner,
    PolicyDecisionRunner,
    HumanPolicy,
    BuyHoldSell,
    DecisionConfig,
)
from sim_platform.execution.models import ExecutionConfig
from sim_platform.examples.market_demo import write_fixture
from support.agents import definition, settings
from support.clock import FakeTime

from support.tools import ReadFixture

from support.tools import request, position, validator, SCHEMA


class ToolTests(unittest.TestCase, ReadFixture):
    def setUp(self):
        self.prepare_reads()

    def execute_request(self, request, observed_position):
        try:
            validated = validate_request(request, self.tools.binding)
            captured = capture_read(
                validated, observed_position, self.capture, observed_position['ordinal']
            )
            return self.tools.execute(captured)
        except ToolError as error:
            return error_response(request, observed_position['ordinal'], observed_position, error)

    def call(self, tool, args=None, number='r1', stamp=80_000_000_000):
        req = request(tool, args, number)
        self.assertTrue(validator.valid(req, SCHEMA['$defs']['ToolRequest'], SCHEMA))
        result = self.execute_request(req, position(stamp))
        self.assertTrue(validator.valid(result, SCHEMA['$defs']['ToolResponse'], SCHEMA), result)
        return result

    def test_quote_old_result_is_detached_and_missing_is_not_zero(self):
        old = self.call('market.quote', {'symbol': 'AAPL'})
        self.assertEqual(old['data']['bid']['price_usd'], '100.0000')
        self.market = replace(self.market, bids=((1_020_000, 7),))
        new = self.call('market.quote', {'symbol': 'AAPL'}, 'r2')
        self.assertEqual(new['data']['bid']['price_usd'], '102.0000')
        self.assertEqual(old['data']['bid']['price_usd'], '100.0000')
        new['data']['bid']['quantity_shares'] = 99
        self.assertEqual(self.market.bids[0][1], 7)
        self.market = replace(self.market, bids=(), asks=(), book_time_ns=None)
        self.history['trades'] = ()
        missing = self.call('market.quote', {'symbol': 'AAPL'})['data']
        self.assertIsNone(missing['bid'])
        self.assertIsNone(missing['last'])
        self.assertEqual(missing['missing'][0]['reason'], 'not_observed')

    def test_missing_order_versions_are_not_replaced_by_account_sequence(self):
        from types import MappingProxyType

        validated = validate_request(request('orders.get', dict(order_id='o1')), self.tools.binding)
        captured = capture_read(validated, position(80_000_000_000), self.capture, '1')
        incomplete = replace(captured, order_versions=MappingProxyType({}))
        with self.assertRaises(KeyError):
            self.tools.execute(incomplete)

    def test_request_validation_stages_have_distinct_rejections(self):
        from sim_platform.domain.json_values import copy_json

        baseline = request('market.quote', dict(symbol='AAPL'))
        cases = [
            (dict(baseline, extra=True), 'INVALID_SCHEMA'),
            (dict(baseline, epoch='2'), 'STALE_WORK'),
            (dict(baseline, tool='unknown'), 'TOOL_UNAVAILABLE'),
            (dict(baseline, arguments=dict(symbol='OTHER')), 'INVALID_ARGUMENT'),
            (dict(baseline, arguments=dict(symbol='AAPL', future=True)), 'INVALID_SCHEMA'),
        ]
        for value, expected in cases:
            with self.subTest(code=expected, request=value):
                with self.assertRaises(ToolError) as raised:
                    validate_request(value, self.tools.binding)
                self.assertEqual(raised.exception.code, expected)
        binding = copy_json(self.tools.binding)
        binding['granted_tools'] = []
        with self.assertRaises(ToolError) as raised:
            validate_request(baseline, binding)
        self.assertEqual(raised.exception.code, 'FORBIDDEN')

    def test_account_reservations_depth_and_one_cutoff_state(self):
        data = self.call('state.snapshot', {'symbol': 'AAPL', 'order_limit': 1})
        self.assertEqual(data['cutoff']['account_sequence'], '7')
        self.assertEqual(data['data']['account']['available_cash_usd'], '500.0000')
        self.assertEqual(data['data']['account']['positions'][0]['available_shares'], 1)
        token = data['data']['orders']['next_cursor']
        page = self.call('orders.list', {'status': 'open', 'limit': 1, 'cursor': token})
        self.assertEqual(page['cutoff'], data['cutoff'])
        self.assertEqual(page['data']['items'][0]['order_id'], 'o2')
        depth = self.call('market.depth', {'symbol': 'AAPL', 'levels': 5})
        self.assertEqual(depth['data']['asks'][0]['quantity_shares'], 20)
        self.assertEqual(self.call('orders.get', {'order_id': 'o1'})['data']['remaining_shares'], 3)
        self.assertEqual(
            self.call('orders.get', {'order_id': 'missing'})['error']['code'], 'ORDER_NOT_FOUND'
        )

    def test_trades_pages_pin_content_and_account_cutoff(self):
        first = self.call('market.trades', {'symbol': 'AAPL', 'limit': 1})
        args = dict(symbol='AAPL', limit=1, cursor=first['data']['next_cursor'])
        self.history['trades'] = ()  # Later correction cannot mutate pinned page.
        self.execution = replace(
            self.execution, account=replace(self.execution.account, sequence=8)
        )
        second = self.call('market.trades', args, 'r2', stamp=90_000_000_000)
        again = self.call('market.trades', args, 'r3', stamp=100_000_000_000)
        self.assertEqual(second['data'], again['data'])
        self.assertEqual(second['cutoff'], first['cutoff'])
        self.assertEqual(second['data']['items'][0]['price_usd'], '102.0000')
        args['limit'] = 2
        self.assertEqual(self.call('market.trades', args)['error']['code'], 'CURSOR_INVALID')

    def test_bar_partial_historical_boundary_and_no_future(self):
        args = dict(
            symbol='AAPL', start_ns='0', end_ns='80000000000', interval_seconds='60', limit=100
        )
        values = self.call('market.bars', args)['data']['items']
        self.assertTrue(values[0]['complete'])
        self.assertFalse(values[1]['complete'])
        self.assertEqual(values[1]['close_usd'], '101.0000')
        args['end_ns'] = '20000000000'
        past = self.call('market.bars', args)['data']['items'][0]
        self.assertEqual(past['high_usd'], '100.0000')
        self.assertEqual(past['volume_shares'], 1)
        args['end_ns'] = '80000000001'
        self.assertEqual(self.call('market.bars', args)['error']['code'], 'FUTURE_DATA')

    def test_bars_page_pins_correction_and_expires_explicitly(self):
        args = dict(
            symbol='AAPL', start_ns='0', end_ns='80000000000', interval_seconds='60', limit=1
        )
        first = self.call('market.bars', args)
        args['cursor'] = first['data']['next_cursor']
        changed = dict(args, start_ns=first['data']['next_start_ns'])
        self.assertEqual(self.call('market.bars', changed)['error']['code'], 'CURSOR_INVALID')
        self.history['bars'] = ()
        second = self.call('market.bars', args, 'r2')
        self.assertEqual(second['cutoff'], first['cutoff'])
        self.assertEqual(second['data']['items'][0]['close_usd'], '101.0000')
        self.tools.pages._pages.clear()
        self.assertEqual(self.call('market.bars', args)['error']['code'], 'CURSOR_INVALID')

    def test_history_retention_does_not_fabricate_partial_data(self):
        self.history.update(trades_complete=False, trade_start_ns=40_000_000_000)
        args = dict(
            symbol='AAPL', start_ns='0', end_ns='20000000000', interval_seconds='60', limit=100
        )
        self.assertEqual(self.call('market.bars', args)['error']['code'], 'DATA_UNAVAILABLE')

    def test_permissions_stale_work_unknown_version_and_scalar_limits(self):
        self.tools.binding['granted_tools'] = []
        req = request('market.quote', {'symbol': 'AAPL'})
        self.assertEqual(self.execute_request(req, position(0))['error']['code'], 'FORBIDDEN')
        req['epoch'] = '2'
        self.assertEqual(self.execute_request(req, position(0))['error']['code'], 'STALE_WORK')
        req['schema_version'] = 'unknown'
        self.assertEqual(self.execute_request(req, position(0))['error']['code'], 'INVALID_SCHEMA')
        self.tools.binding['granted_tools'] = sorted(READ_TOOLS)
        for args in (
            {'symbol': 'AAPL', 'levels': True},
            {'symbol': 'AAPL', 'levels': 6},
            {'symbol': 'MSFT', 'levels': 1},
        ):
            with self.assertRaises(ToolError):
                validate_request(request('market.depth', args), self.tools.binding)
        with self.assertRaises(ToolError):
            validate_request(
                request('market.quote', {'symbol': 'AAPL', 'as_of_ns': '999'}), self.tools.binding
            )

    def test_environment_and_response_limit(self):
        env = self.call('environment.describe')
        self.assertEqual(env['data']['currency'], 'USD')
        self.assertEqual(env['data']['price_scale'], 10000)
        self.assertFalse(env['data']['execution_rules']['short_selling'])
        self.assertEqual(env['data']['source']['scope'], 'single_venue')
        self.tools.binding['limits']['tool_response_bytes'] = 1024
        self.assertEqual(
            self.call('state.snapshot', {'symbol': 'AAPL', 'order_limit': 2})['error']['code'],
            'OUTPUT_TOO_LARGE',
        )


class AgentSessionTests(unittest.TestCase):
    """Use only public Platform/market/session APIs, without ResearchController."""

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = Platform(self.root / 'accounts')
        self.addCleanup(self.app.close)
        self.profile = self.app.create_profile('Direct agent')
        self.account = self.app.create_account(self.profile, 'Cash', 100_000_000)
        path, _ = write_fixture(self.root / 'fixture')
        self.market = self.app._new_research_market('AAPL')
        self.addCleanup(self.market.close)
        self.market.load(path)
        while not self.market.prepare():
            time.sleep(0.005)
        self.market.start_position('first_book')
        self.source = dict(
            symbol='AAPL',
            source_manifest_ref='source-1',
            start_origin=dict(sequence=self.market.snapshot.sequence, time_ns=self.market.time_ns),
        )
        self.app.create_agent(self.profile, definition())
        config = dict(
            settings(),
            start_ns=str(self.market.time_ns),
            end_ns=str(self.market.time_ns + 4_000_000_000),
            granted_tools=sorted(READ_TOOLS),
        )
        self.experiment = self.app.prepare_agent_experiment(
            self.profile, self.account, 'demo', 1, config
        )

    def open(self, source=None):
        return self.app.open_agent_run(
            self.profile,
            self.experiment['id'],
            self.market,
            source=self.source if source is None else source,
            log_path=self.root / 'agent.jsonl',
            execution_mode='controlled',
        )

    def exercise_lifecycle(self, runner):
        self.assertIsInstance(runner, DecisionRunner)
        self.assertTrue(runner.active)
        runner.play()
        runner.pause()
        self.assertTrue(runner.active)
        runner.play()
        runner.stop()
        runner.close()
        self.assertFalse(runner.active)
        self.assertTrue(runner.closed)
        self.assertEqual(self.app.run(self.profile, runner.run_id)['status'], 'stopped')

    def test_common_lifecycle_controls_human_rule_and_agent_without_controller(self):
        for policy, mode in ((HumanPolicy(), 'manual'), (BuyHoldSell(), 'automatic')):
            with self.subTest(policy=mode):
                account = self.app.create_account(self.profile, mode, 100_000_000)
                maker, run = self.app.create_managed_decision_run(
                    self.profile,
                    account,
                    'AAPL',
                    policy,
                    DecisionConfig(mode=mode),
                    ExecutionConfig(),
                )
                market = self.app.open_market('AAPL')
                path, _ = write_fixture(self.root / mode)
                market.load(path)
                runner = self.app.decision_runner(self.profile, run, maker, market, policy)
                self.assertIsInstance(runner, PolicyDecisionRunner)
                self.exercise_lifecycle(runner)
        self.exercise_lifecycle(self.open())

    def test_direct_public_session_reads_archives_and_borrows_market(self):
        with patch.object(
            self.app, 'open_research', side_effect=AssertionError('research dependency')
        ):
            with self.open() as session:
                self.assertTrue(session.active)
                self.assertEqual(session.snapshot()['pipeline']['state'], 'ready')
                session.play()
                response = session.request_tool(
                    request('market.quote', {'symbol': 'AAPL'}, run=session.run_id)
                ).result(2)['result']
                self.assertEqual(response['status'], 'ok')
                session.pause()
                self.assertEqual(session.snapshot()['pipeline']['state'], 'paused')
                session.play()
        self.assertFalse(session.active)
        self.assertNotEqual(self.market.status, 'closed')
        session.close()  # Safe repeated close.
        self.assertEqual(
            self.app.agent_log_archive(self.profile, session.run_id)['end']['status'], 'stopped'
        )
        with self.assertRaisesRegex(ContractError, 'closed'):
            session.play()

    def test_invalid_prepared_origin_rejects_before_creating_run(self):
        wrong = dict(self.source, start_origin=dict(sequence=0, time_ns=self.market.time_ns))
        with self.assertRaisesRegex(ContractError, 'prepared market'):
            self.open(wrong)
        self.assertEqual(self.app.runs(self.profile), [])

    def test_running_pipeline_cannot_replace_tool_or_output_binding(self):
        with self.open() as session:
            with self.assertRaises(ContractError):
                session.request_tool(request('account.snapshot', {}, run=session.run_id))
            session.play()
            with self.assertRaises(ContractError):
                session.pipeline.bind_ready(operation=lambda *_: {}, sink=lambda _: None)


class AgentExperimentTests(unittest.TestCase):
    """One user-facing experiment creation owns configuration, run and resources."""

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = Platform(self.root / 'accounts')
        self.addCleanup(self.app.close)
        self.profile = self.app.create_profile('Experiment owner')
        self.account = self.app.create_account(self.profile, 'Cash', 100_000_000)
        self.path, _ = write_fixture(self.root / 'fixture')
        self.app.create_agent(self.profile, definition())
        self.start = 34_202_500_000_000  # Between the +2s book and +4s trade.
        self.config = dict(
            settings(),
            start_ns=str(self.start),
            end_ns=str(self.start + 4_000_000_000),
            granted_tools=sorted(READ_TOOLS),
        )
        self.now = FakeTime()

    def open(self, **options):
        return self.app.open_agent_experiment(
            self.profile,
            self.account,
            'demo',
            1,
            source=self.path,
            settings=self.config,
            clock=ExperimentClock(self.start, self.now),
            execution_mode='controlled',
            **options,
        )

    def test_generic_entry_and_agent_compatibility_entry_share_setup(self):
        with patch.object(self.app, 'open_research', wraps=self.app.open_research) as compose:
            experiment = self.app.open_experiment(
                self.profile,
                self.account,
                method='agent',
                agent_id='demo',
                revision=1,
                source=self.path,
                settings=self.config,
                clock=ExperimentClock(self.start, self.now),
                execution_mode='controlled',
            )
        compose.assert_called_once()
        self.assertIs(experiment.tool_controls, experiment.runner)
        with self.assertRaises(ContractError):
            experiment.policy_controls
        # Polling/painting cannot advance an agent-owned market.
        with patch.object(experiment.market, 'pump', side_effect=AssertionError('double drive')):
            experiment.tick(max_events=100)
        self.assertEqual(len(self.app._resources._roots), 1)
        experiment.stop()

    def test_one_entry_creates_config_run_and_only_one_root(self):
        with patch.object(
            self.app, 'prepare_agent_experiment', side_effect=AssertionError('public draft call')
        ):
            experiment = self.open()
        self.assertEqual(
            [resource for resource, _ in self.app._resources._roots.values()], [experiment]
        )
        self.assertEqual(len(self.app.agent_experiments(self.profile)), 1)
        self.assertEqual(len(self.app.runs(self.profile)), 1)
        self.assertIsInstance(experiment.runner, DecisionRunner)
        self.assertEqual(experiment.market.time_ns, 34_202_000_000_000)
        self.assertEqual(experiment.runner.snapshot()['pipeline']['executed_ns'], self.start)
        experiment.play()
        result = experiment.request_tool(
            request('market.quote', {'symbol': 'AAPL'}, run=experiment.run_id)
        ).result(2)['result']
        self.assertEqual(result['cutoff']['market_position']['time_ns'], str(self.start))
        self.assertEqual(result['data']['ask']['price_usd'], '190.1000')
        self.assertIsNone(result['data']['last'])  # +4s trade has not happened.
        run = experiment.run_id
        experiment.close()
        self.assertTrue(experiment.runner.closed)
        self.assertEqual(experiment.market.status, 'closed')
        self.assertEqual(self.app._resources._roots, {})
        self.assertEqual(self.app._resources._executions, {})
        self.assertEqual(self.app.agent_log_archive(self.profile, run)['end']['status'], 'stopped')
        self.assertTrue((self.app.directory / 'agent-logs' / (run + '.jsonl')).exists())

    def test_config_and_run_creation_rollback_together(self):
        from sim_platform.application.use_cases.agent_runs import AgentRunService

        with patch.object(
            AgentRunService, 'create_agent_run', side_effect=ContractError('binding rejected')
        ):
            with self.assertRaisesRegex(ContractError, 'binding rejected'):
                self.open()
        self.assertEqual(self.app.agent_experiments(self.profile), [])
        self.assertEqual(self.app.runs(self.profile), [])
        self.assertEqual(self.app._resources._roots, {})

    def test_failed_log_creation_ends_run_without_leaking_children(self):
        log = self.root / 'existing.jsonl'
        log.write_text('original')
        with self.assertRaises(FileExistsError):
            self.open(log_path=log)
        self.assertEqual(log.read_text(), 'original')
        self.assertEqual(self.app.runs(self.profile)[0]['status'], 'stopped')
        self.assertEqual(self.app._resources._roots, {})
        self.assertEqual(self.app._resources._executions, {})

    def test_platform_close_closes_root_then_owned_runner_and_market(self):
        experiment = self.open()
        experiment.play()
        calls = []
        originals = (experiment.close, experiment.runner.close, experiment.market.close)

        def track(label, method):
            def call():
                calls.append(label)
                return method()

            return call

        with (
            patch.object(experiment, 'close', track('experiment', originals[0])),
            patch.object(experiment.runner, 'close', track('runner', originals[1])),
            patch.object(experiment.market, 'close', track('market', originals[2])),
        ):
            self.app.close()
        self.assertEqual(calls, ['experiment', 'runner', 'market'])

    def test_invalid_settings_or_ended_source_do_not_leave_a_run(self):
        self.config['end_ns'] = self.config['start_ns']
        with self.assertRaises(ContractError):
            self.open()
        self.assertEqual(self.app.runs(self.profile), [])
        self.assertEqual(self.app._resources._roots, {})
        self.start = 34_208_000_000_000
        self.config.update(start_ns=str(self.start), end_ns=str(self.start + 4_000_000_000))
        with self.assertRaisesRegex(ContractError, 'no events after'):
            self.open()
        self.assertEqual(self.app.agent_experiments(self.profile), [])
        self.assertEqual(self.app.runs(self.profile), [])
        self.assertEqual(self.app._resources._roots, {})

    def test_output_failure_releases_owned_log_market_and_root(self):
        experiment = self.open()
        recorder = experiment.runner._recorder
        with patch.object(recorder._file, 'append', side_effect=OSError('disk unavailable')):
            experiment.play()
            # Initial work is also logged: disk failure can close ingress before this request.
            try:
                future = experiment.request_tool(
                    request('account.snapshot', {}, run=experiment.run_id)
                )
            except ContractError:
                future = None
            if future is not None:
                with self.assertRaises((OSError, ContractError)):
                    future.result(2)
            with self.assertRaises(ContractError):
                experiment.runner.pipeline.channels.ended.result(2)
            with self.assertRaisesRegex(ContractError, 'output failed'):
                experiment.close()
        self.assertTrue(recorder._file._stream.closed)
        self.assertTrue(experiment.closed)
        self.assertEqual(experiment.market.status, 'closed')
        self.assertEqual(self.app._resources._roots, {})
        self.assertEqual(self.app._resources._executions, {})

    def test_failed_owner_shutdown_retains_storage_and_can_retry(self):
        experiment = self.open()
        experiment.play()
        with (
            patch.object(
                experiment.runner.pipeline, 'close', side_effect=ContractError('owner stuck')
            ),
            patch.object(self.app._database_instance, 'close') as database_close,
            patch.object(self.app._workspace, 'close') as workspace_close,
        ):
            with self.assertRaisesRegex(ContractError, 'owner stuck'):
                self.app.close()
            database_close.assert_not_called()
            workspace_close.assert_not_called()
        self.assertFalse(experiment.closed)
        self.assertNotEqual(experiment.market.status, 'closed')
        self.assertTrue(self.app._resources.has_live_execution)
        self.assertTrue(self.app._resources.has_roots)
        self.app.close()
        self.assertTrue(experiment.closed)
        self.assertFalse(self.app._resources.has_live_execution)
        self.assertFalse(self.app._resources.has_roots)


class AgentResearchTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = Platform(self.root / 'accounts')
        self.addCleanup(self.app.close)
        self.profile = self.app.create_profile('Researcher')
        self.account = self.app.create_account(self.profile, 'Cash', 100_000_000)
        source, _ = write_fixture(self.root / 'fixture')
        self.controller = self.app.open_research(method='agent')
        self.controller.begin_prepare(source)
        while self.controller.preparation['status'] == 'preparing':
            self.controller.prepare_tick()
        self.origin = self.controller.market.time_ns
        self.now = FakeTime()
        config = dict(
            settings(),
            start_ns=str(self.origin),
            end_ns=str(self.origin + 4_000_000_000),
            granted_tools=sorted(READ_TOOLS),
        )
        self.app.create_agent(self.profile, definition())
        self.experiment = self.app.prepare_agent_experiment(
            self.profile, self.account, 'demo', 1, config
        )
        self.settings = config

    def bind(self):
        return self.controller.start_agent(
            self.profile,
            self.experiment['id'],
            source_manifest_ref='source-1',
            log_path=self.root / 'agent.jsonl',
            clock=ExperimentClock(self.origin, self.now),
            execution_mode='controlled',
        )

    def query(self, name='r1', tool='market.quote', args=None):
        future = self.controller.request_tool(
            request(
                tool, {'symbol': 'AAPL'} if args is None else args, name, self.controller.run_id
            )
        )
        if tool not in (
            'environment.describe',
            'market.quote',
            'market.depth',
            'market.trades',
            'market.bars',
        ):
            self.now.ns += 1
            self.controller.runner.pipeline.wake()
        return future

    def test_real_controller_read_pause_resume_end_and_json_archive(self):
        view = self.bind()
        self.assertEqual(view['mode'], 'agent')
        self.assertIsInstance(self.controller.runner, DecisionRunner)
        self.assertIsInstance(self.controller.runner, AgentDecisionRunner)
        self.assertIs(self.controller.runner, self.controller.agent_run)
        bound = self.app.run(self.profile, self.controller.run_id)['config']['agent']
        self.assertEqual(bound['run_id'], self.controller.run_id)
        self.assertEqual(
            self.app.agent_experiment(self.profile, self.experiment['id'])['config']['run_id'],
            self.experiment['id'],
        )
        self.assertTrue(self.controller.active)
        self.controller.play(1)
        first = self.query().result(2)['result']
        self.assertEqual(first['data']['ask']['price_usd'], '190.1000')
        self.assertTrue(validator.valid(first, SCHEMA['$defs']['ToolResponse'], SCHEMA))
        self.controller.runner.pipeline.channels.pause_delivery()
        pending = self.query('r2')
        self.controller.pause()
        self.now.ns = 100_000_000_000
        time.sleep(0.01)
        self.assertFalse(pending.done())
        self.controller.play()
        resumed = pending.result(2)['result']
        self.assertEqual(resumed['receipt_event_index'], '2')
        self.assertEqual(resumed['cutoff']['market_position']['time_ns'], str(self.origin))
        # Disable every transaction boundary: completed data reads must use runtime/cache only.
        with (
            patch.object(
                self.app._db_operations, '_unit_of_work', side_effect=AssertionError('SQL read')
            ),
            patch(
                'sim_platform.market.prepared.native.NativePreparation',
                side_effect=AssertionError('native replay during query'),
            ),
        ):
            for name, tool, args in [
                ('r3', 'account.snapshot', {}),
                ('r4', 'environment.describe', {}),
                ('r5', 'market.trades', {'symbol': 'AAPL', 'limit': 10}),
                ('r6', 'market.quote', {'symbol': 'AAPL'}),
                ('r7', 'market.depth', {'symbol': 'AAPL', 'levels': 5}),
                ('r8', 'state.snapshot', {'symbol': 'AAPL', 'order_limit': 10}),
                (
                    'r9',
                    'market.bars',
                    {
                        'symbol': 'AAPL',
                        'start_ns': str(self.origin // 60_000_000_000 * 60_000_000_000),
                        'end_ns': str(self.origin),
                        'interval_seconds': '60',
                        'limit': 10,
                    },
                ),
                ('r10', 'orders.list', {'limit': 10, 'status': 'all'}),
            ]:
                self.assertEqual(self.query(name, tool, args).result(2)['result']['status'], 'ok')
        self.now.ns += 4_000_000_000
        self.controller.agent_run.pipeline._wake.set()
        self.controller.agent_run.pipeline.channels.ended.result(2)
        self.assertFalse(self.controller.active)
        self.assertEqual(self.app.run(self.profile, self.controller.run_id)['status'], 'completed')
        archive = self.app.agent_log_archive(self.profile, self.controller.run_id)
        tools = [r for r in archive['records'] if r['kind'] == 'tool']
        self.assertEqual(len(tools), 10)
        for key in ('sequence', 'time_ns', 'boundary'):
            self.assertEqual(
                tools[0]['position'][key], tools[0]['result']['cutoff']['market_position'][key]
            )
        self.assertTrue((self.root / 'agent.jsonl').exists())
        self.assertEqual(self.controller.market.snapshot.sequence, 5)  # end +6s B frame excluded
        destination = self.root / 'agent-replay'
        with self.assertRaisesRegex(ContractError, 'operation Replay'):
            Platform.replay_run(self.root / 'accounts', self.controller.run_id, destination)
        self.assertFalse(destination.exists())

    def test_controller_tick_cannot_drive_agent_and_policy_controls_reject(self):
        self.bind()
        before = self.controller.market.snapshot.sequence
        self.controller.tick(max_events=1000)
        self.assertEqual(self.controller.market.snapshot.sequence, before)
        for name in ('order', 'cancel', 'handoff', 'start'):
            self.assertFalse(hasattr(self.controller, name))
        for call in (self.controller.step, self.controller.cancel_preparation):
            with self.assertRaises(ContractError):
                call()
        self.controller.stop()
        self.assertEqual(self.app.run(self.profile, self.controller.run_id)['status'], 'stopped')
        self.assertEqual(
            self.app.agent_log_archive(self.profile, self.controller.run_id)['end']['status'],
            'stopped',
        )

    def test_source_mismatch_is_atomic_and_changed_account_rejected(self):
        with self.assertRaises(ContractError):
            self.controller.start_agent(
                self.profile,
                self.experiment['id'],
                source_manifest_ref='other',
                log_path=self.root / 'agent.jsonl',
                execution_mode='controlled',
            )
        self.assertEqual(self.app.runs(self.profile), [])
        self.assertEqual(self.app.makers(self.profile), [])
        other = self.app.create_maker(self.profile, 'Other', 'human', '1')
        run = self.app.create_run(self.profile, self.account, other, ['AAPL'])
        self.app.finish_run(self.profile, run)
        # No account mutation occurred, so this still permits binding.
        self.bind()
        self.controller.stop()

    def test_failed_log_start_stops_run_and_releases_market(self):
        (self.root / 'agent.jsonl').write_text('existing')
        with self.assertRaises(FileExistsError):
            self.bind()
        self.assertFalse(self.controller.market.has_execution_owner)
        self.assertEqual(self.app.runs(self.profile)[0]['status'], 'stopped')
        self.assertEqual((self.root / 'agent.jsonl').read_text(), 'existing')

    def test_source_binding_failure_ends_unbound_run_without_financial_operations(self):
        with patch.object(
            self.app._operations, 'replay_source', side_effect=OSError('source unavailable')
        ):
            with self.assertRaises(OSError):
                self.bind()
        self.assertFalse(self.controller.market.has_execution_owner)
        self.assertEqual(self.app.runs(self.profile)[0]['status'], 'stopped')
        self.assertEqual(self.app.snapshot(self.profile, self.account).cash, 100_000_000)

    def test_changed_account_cannot_start_the_saved_experiment(self):
        maker = self.app.create_maker(self.profile, 'Other', 'human', '1')
        run = self.app.create_execution_run(self.profile, self.account, maker, 'AAPL')
        market = self.app.open_market()
        market.load(self.root / 'fixture/aapl.bin')
        execution = self.app.execution_session(self.profile, run, maker, market)
        execution.play()
        execution.pump(3)
        execution.submit(Intent('buy', 'AAPL', 'buy', 1, 1_901_000))
        execution.pump(1)
        execution.close()
        before = len(self.app.runs(self.profile))
        with self.assertRaisesRegex(ContractError, 'account changed'):
            self.bind()
        self.assertEqual(len(self.app.runs(self.profile)), before)

    def test_native_broken_trade_keeps_old_page_and_corrects_new_reads(self):
        self.experiment = self.app.prepare_agent_experiment(
            self.profile,
            self.account,
            'demo',
            1,
            dict(
                self.settings,
                end_ns=str(self.origin + 6_000_000_000),
                lag=dict(self.settings['lag'], threshold_seconds='10'),
            ),
        )
        self.bind()
        self.controller.play()
        self.now.ns = 3_000_000_000  # E at +2s, P at +3s from warm origin.
        first = self.query('page-1', 'market.trades', {'symbol': 'AAPL', 'limit': 1}).result(2)[
            'result'
        ]
        token = first['data']['next_cursor']
        self.assertIsNotNone(token)
        self.now.ns = 4_000_000_000  # B correction removes P match 102.
        old = self.query(
            'page-2', 'market.trades', {'symbol': 'AAPL', 'limit': 1, 'cursor': token}
        ).result(2)['result']
        self.assertEqual(old['data']['items'][0]['match_id'], '102')
        self.assertEqual(old['cutoff'], first['cutoff'])
        fresh = self.query('fresh', 'market.trades', {'symbol': 'AAPL', 'limit': 100}).result(2)[
            'result'
        ]
        self.assertEqual([t['match_id'] for t in fresh['data']['items']], ['101'])
        bars = self.query(
            'bars',
            'market.bars',
            {
                'symbol': 'AAPL',
                'start_ns': str(self.origin // 60_000_000_000 * 60_000_000_000),
                'end_ns': str(self.origin + 4_000_000_000),
                'interval_seconds': '60',
                'limit': 100,
            },
        ).result(2)['result']
        self.assertEqual(bars['data']['items'][0]['trade_count'], 1)

    def test_close_stops_pipeline_before_market_and_keeps_saved_operations(self):
        self.bind()
        self.controller.play()
        self.query().result(2)
        run = self.controller.run_id
        self.controller.close()
        self.assertTrue(self.controller.market.status == 'closed')
        self.assertEqual(self.app.agent_log_archive(self.profile, run)['end']['status'], 'stopped')


class HistoryWindowTests(unittest.TestCase):
    def test_raw_tail_limit_does_not_truncate_cached_minute_volume(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            source, _ = write_fixture(root / 'fixture')
            original = source.read_bytes()
            offset = 0
            for _ in range(3):
                offset += 2 + int.from_bytes(original[offset : offset + 2], 'big')
            origin = 34_200_000_000_000
            records = [original[:offset]]
            for identity in range(1, 1003):
                stamp = origin + 4_000_000_000 + identity
                common = b'P' + struct.pack('>HH', 7, 0) + stamp.to_bytes(6, 'big')
                payload = common + struct.pack('>Q', 0) + b'B' + struct.pack('>I', 1) + b'AAPL    '
                payload += struct.pack('>IQ', 1_000_000, identity)
                records.append(struct.pack('>H', len(payload)) + payload)
            source.write_bytes(b''.join(records) + b'\0\0')
            with Platform(root / 'accounts') as app:
                market = app.open_market()
                market.load(source)
                for _ in range(3):
                    market.step()
                market.prepare_agent_history()
                market.play()
                market.pump(1001)
                # Other interval queries must not evict the active minute projection.
                for seconds in range(2, 11):
                    market.bars(seconds * 1_000_000_000, limit=1)
                market.pump(1)
                history = market.agent_history_window()
                self.assertEqual(len(history['trades']), 1000)
                self.assertEqual(history['trades'][0].match_id, 3)
                self.assertFalse(history['trades_complete'])
                self.assertEqual(history['bars'][0].volume, 1002)
                self.assertEqual(history['bars'][0].count, 1002)
