"""Hand-calculated financial tools through the real owner and native market."""

from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch

from sim_platform import Platform, ContractError
from sim_platform.execution import ExecutionConfig
from sim_platform.agents.tools.protocol.contracts import IMPLEMENTED_TOOLS, ToolError
from support.clock import TestClock as ExperimentClock
from sim_platform.agents.hosting.host import AgentProcessHost
from support.agents import definition, settings
from support.clock import FakeTime, until
from support.tools import validator, SCHEMA

from support.trading import ORIGIN, SECOND, fixture, wire


def trading_program(client, config):
    buy = wire(
        config,
        'orders.submit_limit',
        dict(symbol='AAPL', side='buy', quantity_shares=5, limit_price_usd='100.0000'),
        'process-buy',
    )
    first = client.request_tool(buy)
    if first['status'] != 'ok' or first['data']['outcome'] != 'accepted':
        raise RuntimeError('financial submit failed')
    if client.request_tool(buy) != first:
        raise RuntimeError('duplicate submit changed result')
    result = client.request_tool(
        wire(
            config,
            'orders.cancel',
            dict(order_id=first['data']['order']['order_id']),
            'process-cancel',
        )
    )
    if result['data']['outcome'] != 'cancelled':
        raise RuntimeError('cancel failed')
    original = client.request_tool(
        wire(config, 'requests.get', dict(target_request_id='process-buy'), 'process-lookup')
    )
    if original['data']['original_response'] != first:
        raise RuntimeError('lookup lost original result')
    client.wait_stopped()


class AgentTradingTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = Platform(self.root / 'accounts')
        self.addCleanup(self.app.close)
        self.profile = self.app.create_profile('Trading')
        self.account = self.app.create_account(self.profile, 'Cash', 10_000_000)
        self.app.create_agent(self.profile, definition())
        self.now = FakeTime()
        self.config = dict(
            settings(),
            start_ns=str(ORIGIN + 2 * SECOND),
            end_ns=str(ORIGIN + 8 * SECOND),
            granted_tools=sorted(IMPLEMENTED_TOOLS),
        )
        self.serial = 0

    def open(self, **options):
        source = fixture(self.root / 'source.bin', **options.pop('fixture_options', {}))
        self.experiment = self.app.open_experiment(
            self.profile,
            self.account,
            method='agent',
            agent_id='demo',
            revision=1,
            source=source,
            settings=self.config,
            clock=ExperimentClock(ORIGIN + 2 * SECOND, self.now),
            execution_mode='controlled',
            **options,
        )
        self.runner = self.experiment.runner
        return self.experiment

    def request(self, tool, args=None, name=None):
        self.serial += 1
        return wire(
            dict(run_id=self.runner.run_id), tool, args or {}, name or 'r' + str(self.serial)
        )

    def call(self, tool, args=None, name=None):
        future = self.experiment.request_tool(self.request(tool, args, name))
        self.now.ns += 1  # Seal the receipt tick before the financial owner claims it.
        self.experiment.runner.pipeline.wake()
        result = future.result(timeout=2)['result']
        self.assertTrue(validator.valid(result, SCHEMA['$defs']['ToolResponse'], SCHEMA), result)
        return result

    def buy(self, quantity=5, price='100.0000', name=None):
        return self.call(
            'orders.submit_limit',
            dict(symbol='AAPL', side='buy', quantity_shares=quantity, limit_price_usd=price),
            name,
        )

    def advance(self, seconds=1):
        self.now.ns += seconds * SECOND
        return self.call('account.snapshot')['data']

    def test_pending_has_no_reservation_partial_fill_cancel_releases_only_remainder(self):
        self.open()
        with self.assertRaises(ContractError):
            self.experiment.request_tool(
                self.request(
                    'orders.submit_limit',
                    dict(symbol='AAPL', side='buy', quantity_shares=5, limit_price_usd='100.0000'),
                )
            )
        self.experiment.play()
        future = self.experiment.request_tool(
            self.request(
                'orders.submit_limit',
                dict(symbol='AAPL', side='buy', quantity_shares=5, limit_price_usd='100.0000'),
            )
        )
        time.sleep(0.01)
        self.assertFalse(future.done())
        self.assertEqual(self.app.snapshot(self.profile, self.account).orders, ())
        self.now.ns += 1
        self.runner.pipeline.wake()
        accepted = future.result(timeout=2)['result']
        self.assertEqual(accepted['data']['outcome'], 'accepted')
        before = self.call('account.snapshot')['data']
        self.assertEqual(
            (before['cash_usd'], before['reserved_cash_usd'], before['available_cash_usd']),
            ('1000.0000', '500.0000', '500.0000'),
        )
        after = self.advance()
        self.assertEqual(
            (after['cash_usd'], after['reserved_cash_usd'], after['available_cash_usd']),
            ('800.0000', '300.0000', '500.0000'),
        )
        cancelled = self.call('orders.cancel', dict(order_id=accepted['data']['order']['order_id']))
        self.assertEqual(cancelled['data']['order']['filled_shares'], 2)
        self.assertEqual(cancelled['data']['order']['status'], 'cancelled')
        self.assertEqual(self.call('account.snapshot')['data']['available_cash_usd'], '800.0000')

    def test_original_response_after_fill_lookup_conflict_and_no_double_reservation(self):
        self.open().play()
        first = self.buy(name='buy')
        self.advance()
        repeated = self.buy(name='buy')
        self.assertEqual(repeated, first)
        lookup = self.call('requests.get', dict(target_request_id='buy'))
        self.assertEqual(lookup['data']['original_response'], first)
        self.assertEqual(self.buy(quantity=4, name='buy')['error']['code'], 'REQUEST_ID_CONFLICT')
        self.assertEqual(
            self.call('orders.get', dict(order_id=first['data']['order']['order_id']), name='buy')[
                'error'
            ]['code'],
            'REQUEST_ID_CONFLICT',
        )
        self.assertEqual(len(self.app.snapshot(self.profile, self.account).orders), 1)
        self.assertFalse(
            self.call('requests.get', dict(target_request_id='missing'))['data']['found']
        )

    def test_two_orders_share_supply_and_insufficient_cash_is_a_completed_rejection(self):
        self.open(fixture_options=dict(quantity=100)).play()
        first = self.buy(80)
        second = self.buy(80)
        # Current cash is $1,000, so the $8,000 reservation is rejected.
        self.assertEqual(first['data']['outcome'], 'rejected')
        self.assertEqual(second['status'], 'ok')
        self.assertIsNotNone(second['data']['reason_code'])

    def test_sufficient_funds_two_orders_cannot_replenish_same_supply(self):
        self.account = self.app.create_account(self.profile, 'Funded', 210_000_000)
        self.open(fixture_options=dict(quantity=100)).play()
        self.buy(80)
        self.buy(80)
        self.advance()
        orders = self.app.snapshot(self.profile, self.account).orders
        self.assertEqual(sorted(o.filled for o in orders), [20, 80])
        self.advance()
        self.assertEqual(
            sum(o.filled for o in self.app.snapshot(self.profile, self.account).orders), 100
        )

    def test_removed_price_cannot_fill_and_limit_can_improve_price(self):
        self.open(fixture_options=dict(disappear=True)).play()
        self.buy()
        self.advance()
        self.assertEqual(self.app.snapshot(self.profile, self.account).orders[0].filled, 0)

    def test_price_improvement_and_fees_release_unused_reservation(self):
        self.open(
            fixture_options=dict(quantity=100, price=1_020_000),
            execution_config=ExecutionConfig(fee_per_share=100),
        ).play()
        self.buy(price='105.0000')
        after = self.advance()
        self.assertEqual(
            (after['cash_usd'], after['reserved_cash_usd'], after['fees_usd']),
            ('489.9500', '0.0000', '0.0500'),
        )

    def test_sell_reservations_and_order_versions_ignore_unrelated_operations(self):
        self.open().play()
        buy = self.buy()
        self.advance()
        self.call('orders.cancel', dict(order_id=buy['data']['order']['order_id']))
        args = dict(symbol='AAPL', side='sell', quantity_shares=1, limit_price_usd='99.0000')
        sell = self.call('orders.submit_limit', args)
        before = sell['data']['order']['version']
        rejected = self.call('orders.submit_limit', dict(args, quantity_shares=2))
        self.assertEqual(rejected['data']['outcome'], 'rejected')
        current = self.call('orders.get', dict(order_id=sell['data']['order']['order_id']))
        self.assertEqual(current['data']['version'], before)
        account = self.call('account.snapshot')['data']['positions'][0]
        self.assertEqual(
            (account['quantity_shares'], account['reserved_shares'], account['available_shares']),
            (2, 1, 1),
        )

    def test_effective_clock_and_latency_do_not_reuse_old_source_timestamp(self):
        self.open(execution_config=ExecutionConfig(latency_ns=SECOND)).play()
        self.now.ns = SECOND // 2
        result = self.buy()
        self.assertEqual(
            result['cutoff']['market_position']['time_ns'], str(ORIGIN + 2 * SECOND + SECOND // 2)
        )
        after = self.advance(seconds=1)
        # Source at +3s cannot fill the +2.5s order eligible only at +3.5s.
        self.assertEqual(after['cash_usd'], '1000.0000')
        self.advance(seconds=1)
        self.assertEqual(self.app.snapshot(self.profile, self.account).orders[0].filled, 2)
        accepted = next(
            e
            for e in self.app.run_events(self.profile, self.runner.run_id)
            if e['kind'] == 'accepted'
        )
        self.assertEqual(accepted['data']['eligible_ns'], ORIGIN + 3 * SECOND + SECOND // 2)
        self.assertEqual(
            accepted['data']['agent_position']['time_ns'], str(ORIGIN + 2 * SECOND + SECOND // 2)
        )
        self.assertEqual(accepted['data']['position']['time_ns'], ORIGIN + 2 * SECOND)

    def test_validation_permission_and_preflight_response_limit(self):
        self.config['limits']['tool_response_bytes'] = 1024
        self.config['source_manifest_ref'] = 's' * 128
        self.open().play()
        for qty, price in [
            (True, '100.0000'),
            (1.5, '100.0000'),
            (1, '100.00001'),
            (1, 100),
            (2**63 - 1, '100.0000'),
        ]:
            with self.assertRaises(ToolError):
                self.buy(quantity=qty, price=price)
        response = self.buy(name='x' * 128)
        self.assertEqual(response['error']['code'], 'OUTPUT_TOO_LARGE')
        self.assertEqual(self.app.snapshot(self.profile, self.account).orders, ())

    def test_formal_process_can_submit_duplicate_cancel_and_lookup(self):
        original = AgentProcessHost

        def host(submit, config, limits, **options):
            return original(submit, config, limits, program=trading_program, **options)

        with patch('sim_platform.agents.hosting.host.AgentProcessHost', host):
            self.open().play()

        def delivered():
            self.now.ns += 1
            self.runner.pipeline.wake()
            process = self.runner.snapshot()['agent_process']
            self.assertNotEqual(process['state'], 'failed', process)
            return process['transport'].get('delivered', 0) == 4

        until(delivered)
        orders = self.app.snapshot(self.profile, self.account).orders
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0].status, 'cancelled')
        self.experiment.close()
        archive = self.app.agent_log_archive(self.profile, self.runner.run_id)
        self.assertEqual(len([r for r in archive['records'] if r['kind'] == 'tool']), 4)

    def test_failed_financial_commit_rolls_back_and_freezes_without_cancelling_old_order(self):
        self.open().play()
        self.buy(name='committed')
        before = self.app.snapshot(self.profile, self.account)
        with patch(
            'sim_platform.execution.service.ExecutionService.complete_change',
            side_effect=RuntimeError('injected commit boundary failure'),
        ):
            future = self.experiment.request_tool(
                self.request(
                    'orders.submit_limit',
                    dict(symbol='AAPL', side='buy', quantity_shares=1, limit_price_usd='100.0000'),
                )
            )
            self.now.ns += 1
            self.runner.pipeline.wake()
            with self.assertRaises(ContractError):
                future.result(timeout=2)
            until(lambda: self.runner.output_finished)
        self.assertEqual(self.runner.pipeline.snapshot()['state'], 'failed')
        self.assertEqual(self.app.snapshot(self.profile, self.account), before)
        self.assertEqual(self.app.run(self.profile, self.runner.run_id)['status'], 'running')
        with self.assertRaises(ContractError):
            self.experiment.close()
        # Financially unresolved run is deliberately not falsely archived as
        # fully failed/cleaned up. Its sealed JSON is retained for recovery.
        self.assertIsNone(self.app.agent_log_archive(self.profile, self.runner.run_id))

    def test_financial_tools_bypass_session_submission_and_require_owner(self):
        self.open().play()
        with self.assertRaises(ContractError):
            self.runner.pipeline.require_owner()
        with (
            patch(
                'sim_platform.execution.session.ExecutionSession.submit',
                side_effect=AssertionError('tools must not submit through session'),
            ),
            patch(
                'sim_platform.execution.session.ExecutionSession.cancel',
                side_effect=AssertionError('tools must not cancel through session'),
            ),
        ):
            accepted = self.buy()
            result = self.call(
                'orders.cancel', dict(order_id=accepted['data']['order']['order_id'])
            )
        self.assertEqual(result['data']['outcome'], 'cancelled')

    def test_read_request_is_validated_once_before_owner_capture(self):
        from sim_platform.agents.tools.server import router as worker

        self.open().play()
        with patch.object(worker, 'validate_request', wraps=worker.validate_request) as validate:
            self.call('market.quote', dict(symbol='AAPL'))
        self.assertEqual(validate.call_count, 1)

    def test_invalid_arguments_never_reach_capture_or_submission(self):
        from sim_platform.application.experiments.agent_driver import AgentToolCapture

        self.open().play()
        with (
            patch.object(
                AgentToolCapture,
                '__call__',
                side_effect=AssertionError('capture before validation'),
            ),
            patch.object(
                self.app._operations,
                'commit_decision',
                side_effect=AssertionError('invalid submission'),
            ),
        ):
            with self.assertRaises(ToolError):
                self.buy(quantity=True)
            with self.assertRaises(ToolError):
                self.call('market.depth', dict(symbol='OTHER', levels=1))
        self.assertEqual(self.app.snapshot(self.profile, self.account).orders, ())

    def test_coordinator_rejects_wrong_source_position_before_financial_effects(self):
        self.open().play()
        observed = self.call('account.snapshot')
        position = dict(observed['cutoff']['market_position'])
        position['sequence'] = str(int(position['sequence']) + 1)
        before = self.app.snapshot(self.profile, self.account)
        maker = self.app.run(self.profile, self.runner.run_id)['maker_id']
        with self.assertRaisesRegex(ContractError, 'source prefix'):
            self.app._operations.commit_decision(
                self.profile,
                self.runner.run_id,
                maker,
                self.runner.pipeline.driver.market.snapshot,
                position=position,
                action=dict(
                    kind='submit',
                    intent=dict(
                        client_id='wrong-position',
                        symbol='AAPL',
                        side='buy',
                        quantity=1,
                        limit_price=1_000_000,
                    ),
                ),
            )
        self.assertEqual(self.app.snapshot(self.profile, self.account), before)

    def test_human_and_agent_share_commit_path_without_agent_policy_audit(self):
        from sim_platform.decisions import service
        from sim_platform.experiments.execution.market import ExecutionCommands

        original = ExecutionCommands.commit
        port_calls = []

        def commit_port(port, maker, market, record=None, expected_state=None, **kwargs):
            port_calls.append((record, kwargs))
            return original(port, maker, market, record, expected_state, **kwargs)

        with (
            patch.object(
                self.app._operations, 'commit_decision', wraps=self.app._operations.commit_decision
            ) as commit,
            patch.object(service, 'execute_action', wraps=service.execute_action) as dispatch,
            patch.object(ExecutionCommands, 'commit', commit_port),
        ):
            self.open().play()
            journal = self.app.run(self.profile, self.runner.run_id)['policy_state']
            accepted = self.buy()
            self.call('orders.cancel', dict(order_id=accepted['data']['order']['order_id']))
            self.assertEqual(
                self.app.run(self.profile, self.runner.run_id)['policy_state'], journal
            )
            self.experiment.close()
            human = self.app.open_experiment(
                self.profile,
                self.account,
                method='human',
                source=self.root / 'source.bin',
                execution_mode='controlled',
            )
            human.policy_controls.order('human-buy', 'buy', 1, 1_000_000)
            human.policy_controls.cancel('human-buy')
            self.assertEqual(commit.call_count, 4)
            self.assertEqual(dispatch.call_count, 4)
            self.assertEqual(len(port_calls), 4)
            self.assertIsNone(port_calls[0][0])
            self.assertIsNotNone(port_calls[-1][0])
            self.assertIsNotNone(dispatch.call_args_list[0].kwargs['position'])
            self.assertNotIn('position', dispatch.call_args_list[-1].kwargs)
            human.close()

    def test_commit_rejects_mixed_tool_and_policy_context_without_financial_effects(self):
        self.open().play()
        position = self.call('account.snapshot')['cutoff']['market_position']
        before = self.app.snapshot(self.profile, self.account)
        maker = self.app.run(self.profile, self.runner.run_id)['maker_id']
        with self.assertRaisesRegex(ContractError, 'only action and position'):
            self.app._operations.commit_decision(
                self.profile,
                self.runner.run_id,
                maker,
                self.runner.pipeline.driver.market.snapshot,
                record={},
                expected_state={},
                action=dict(kind='cancel', order_id='missing'),
                position=position,
            )
        self.assertEqual(self.app.snapshot(self.profile, self.account), before)

    def test_cancel_pending_error_replay_and_operation_capacity(self):
        self.open().play()
        missing = self.call('orders.cancel', dict(order_id='missing'), name='bad-cancel')
        self.assertEqual(missing['error']['code'], 'ORDER_NOT_FOUND')
        self.assertEqual(
            self.call('orders.cancel', dict(order_id='missing'), name='bad-cancel'), missing
        )
        with patch('sim_platform.agents.tools.trading.requests.MAX_FINANCIAL_REQUESTS', 2):
            first = self.buy(name='buy')
            self.assertEqual(self.buy(name='extra')['error']['code'], 'QUEUE_FULL')
            self.assertEqual(self.buy(name='buy'), first)
        future = self.experiment.request_tool(
            self.request('orders.cancel', dict(order_id=first['data']['order']['order_id']))
        )
        self.now.ns += 1
        self.runner.pipeline.wake()
        future.result(timeout=2)
        self.assertEqual(self.call('account.snapshot')['data']['reserved_cash_usd'], '0.0000')

    def test_tools_exit_after_commit_preserves_result_orders_and_lookup(self):
        self.open().play()
        worker = self.runner._tools._worker
        submit = worker.submit

        def exit_before_enqueue(captured):
            worker._error = 'injected transport exit after commit'
            worker._process.terminate()
            return submit(captured)

        with patch.object(worker, 'submit', exit_before_enqueue):
            accepted = self.buy(name='committed')
        self.assertEqual(accepted['data']['outcome'], 'accepted')
        self.assertEqual(self.buy(name='committed'), accepted)
        lookup = self.call('requests.get', dict(target_request_id='committed'))
        self.assertEqual(lookup['data']['original_response'], accepted)
        self.assertEqual(len(self.app.snapshot(self.profile, self.account).orders), 1)
        self.assertEqual(self.app.snapshot(self.profile, self.account).orders[0].status, 'accepted')
        self.assertEqual(self.runner.pipeline.snapshot()['state'], 'running')
        rejected = self.buy(name='new-after-crash')
        self.assertEqual(rejected['error']['code'], 'TOOL_UNAVAILABLE')
        self.assertEqual(len(self.app.snapshot(self.profile, self.account).orders), 1)

    def test_submit_permission_is_checked_before_financial_effects(self):
        self.config['granted_tools'].remove('orders.submit_limit')
        self.open().play()
        with self.assertRaises(ToolError):
            self.buy()
        self.assertEqual(self.app.snapshot(self.profile, self.account).orders, ())

    def test_cancel_terminal_order_has_no_second_financial_event(self):
        self.open().play()
        accepted = self.buy(quantity=2)
        self.advance()
        before = self.call('account.snapshot')['data']
        cancelled = self.call('orders.cancel', dict(order_id=accepted['data']['order']['order_id']))
        self.assertEqual(cancelled['data']['outcome'], 'already_terminal')
        self.assertEqual(cancelled['data']['order']['status'], 'filled')
        self.assertEqual(self.call('account.snapshot')['data'], before)

    def test_stop_preserves_partial_fill_and_releases_remainder(self):
        self.open().play()
        self.buy()
        self.advance()
        self.experiment.stop()
        snapshot = self.app.snapshot(self.profile, self.account)
        self.assertEqual((snapshot.orders[0].filled, snapshot.orders[0].status), (2, 'cancelled'))
        self.assertEqual(snapshot.cash, 8_000_000)
        self.assertEqual(self.runner.pipeline.driver.execution.snapshot.reserved_cash, 0)
        self.experiment.close()
        self.assertEqual(
            self.app.agent_log_archive(self.profile, self.runner.run_id)['end']['status'], 'stopped'
        )

    def test_end_preserves_fills_and_cancels_only_remainder(self):
        self.open().play()
        self.buy()
        self.advance()
        self.now.ns = 6 * SECOND
        until(lambda: not self.runner.active)
        self.experiment.close()
        order = self.app.snapshot(self.profile, self.account).orders[0]
        self.assertEqual((order.filled, order.status), (2, 'cancelled'))
        archive = self.app.agent_log_archive(self.profile, self.runner.run_id)
        self.assertEqual(archive['end']['status'], 'completed')


if __name__ == '__main__':
    unittest.main()
