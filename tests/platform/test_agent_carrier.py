"""Built-in template behavior and full Platform/Agent/Tools/Runtime integration."""

import asyncio
import json
import os
import time
import unittest

from support.carrier import AgentExperimentFixture, ScriptClient, template_configuration, call
from sim_platform import ContractError
from sim_platform.agents.carrier.conversation import Conversation
from sim_platform.agents.carrier.program import ConfiguredAgentProgram
from sim_platform.agents.carrier.client import AgentClient
from sim_platform.agents.carrier.agent import TradingAgent
from sim_platform.agents.carrier.model import ResponsesModel, validate_response
from sim_platform.agents.tools.client.collection import TOOL_TYPES, built_in_tools
from sim_platform.agents.tools.client.market import MarketQuoteTool
from sim_platform.agents.tools.client.control import FinishWorkTool
from sim_platform.agents.tools.protocol.contracts import IMPLEMENTED_TOOLS, ARGUMENTS, ToolError
from sim_platform.models.contracts import ModelProfile, ProviderError, validate_input
from sim_platform.models.providers.openai import OpenAIResponses
from support.clock import until, FakeTime, TestClock
from support import prepared_market as prepared


class TemplateTests(unittest.TestCase):
    def test_provider_forwards_tool_payload_without_server_history_or_truncation(self):
        provider = OpenAIResponses()
        sent = []

        async def capture(payload, secret, received):
            sent.append(payload)
            return dict(output=[])

        provider._send = capture
        config = template_configuration()
        config['template']['granted_tools'] = ['market.quote']
        client = AgentClient(ScriptClient([]), config)
        payload = ResponsesModel.payload(
            Conversation('original prompt', {}), built_in_tools(client), client.limits
        )
        asyncio.run(
            provider.request(
                ModelProfile('openai-responses', 'mock', 'fixture'),
                validate_input(payload),
                'not-a-real-key',
            )
        )
        self.assertEqual(sent[0]['input'], payload['input'])
        self.assertEqual(sent[0]['tools'], payload['tools'])
        self.assertFalse(sent[0]['parallel_tool_calls'])
        self.assertFalse(sent[0]['store'])
        self.assertEqual(sent[0]['truncation'], 'disabled')

    def test_tool_objects_expose_only_grants_and_own_their_arguments(self):
        self.assertEqual(set(TOOL_TYPES), IMPLEMENTED_TOOLS)
        config = template_configuration()
        config['template']['granted_tools'] = ['market.quote', 'model.request']
        client = AgentClient(ScriptClient([]), config)
        tools = built_in_tools(client)
        self.assertEqual([t.function_name for t in tools], ['environment_describe', 'market_quote'])
        for cls in TOOL_TYPES.values():
            tool = cls(client)
            schema = tool.input_schema(client.limits)
            self.assertEqual(
                set(schema['properties']),
                set(cls.required_arguments()) | set(cls.optional_arguments()),
            )
            self.assertEqual(schema['required'], list(ARGUMENTS[cls.name]))
        payload = ResponsesModel.payload(Conversation('prompt', {}), tools, client.limits)
        self.assertEqual(validate_input(payload), payload)
        for bad in ([dict(type='web_search')], 'tools', [dict(type='function')]):
            with self.assertRaises(ContractError):
                validate_input(dict(input='hello', tools=bad))

    def test_client_hides_ids_and_preserves_authoritative_validation(self):
        config = template_configuration()
        config['template']['granted_tools'] = ['market.quote']
        transport = ScriptClient([])
        client = AgentClient(transport, config)
        with self.assertRaisesRegex(ContractError, 'receive a task'):
            MarketQuoteTool(client).run('AAPL')
        task = client.next_task('Check opportunities')
        self.assertEqual(task.instruction, 'Check opportunities')
        self.assertNotIn('work_id', task.notification)
        for name, args, code in [
            ('unknown', {}, 'TOOL_UNAVAILABLE'),
            ('orders.cancel', {'order_id': 'x'}, 'FORBIDDEN'),
            ('market.quote', {'symbol': 'MSFT'}, 'INVALID_ARGUMENT'),
            ('market.quote', {'symbol': 'AAPL', 'secret': 'x'}, 'INVALID_SCHEMA'),
        ]:
            with self.assertRaises(ToolError) as error:
                client.call_tool(name, args)
            self.assertEqual(error.exception.code, code)
        result = MarketQuoteTool(client).run(symbol='AAPL')
        self.assertEqual(result['status'], 'ok')
        req = transport.requests[-1]
        self.assertEqual((req['work_id'], req['epoch'], req['run_id']), ('work-1', '1', 'run-1'))
        self.assertEqual(req['tool'], 'market.quote')
        self.assertEqual(req['arguments'], {'symbol': 'AAPL'})
        with self.assertRaises(ToolError):
            MarketQuoteTool(client).invoke({'symbol': 'AAPL', 'extra': 1})

    def test_agent_run_accepts_a_task_and_returns_its_completion_result(self):
        transport = ScriptClient([dict(output=[call('control.finish', 1)])])
        config = template_configuration()
        client = AgentClient(transport, config)
        task = client.next_task('Inspect the opportunity; no trade required.')
        agent = TradingAgent(
            model=ResponsesModel(client), tools=[FinishWorkTool(client)], instructions='Be careful.'
        )
        result = agent.run(task)
        self.assertEqual(result['status'], 'ok')
        self.assertTrue(
            any(
                x.get('content') == 'Task: ' + task.instruction
                for x in transport.payloads[0]['input']
            )
        )
        self.assertEqual([r['tool'] for r in transport.requests], ['control.finish'])

    def test_context_keeps_system_reasoning_calls_results_and_previous_round(self):
        reasoning = dict(type='reasoning', id='reason-1', summary=[], encrypted_content='opaque')
        quote = call('market.quote', 1, dict(symbol='AAPL'))
        client = ScriptClient(
            [
                dict(output=[reasoning, quote]),
                dict(output=[call('control.finish', 2)]),
                dict(output=[call('control.finish', 3)]),
            ],
            rounds=2,
        )
        ConfiguredAgentProgram()(client, template_configuration())
        first, second, third = client.payloads
        self.assertEqual(
            first['input'][0], dict(role='system', content='Original user system prompt.')
        )
        self.assertIn(reasoning, second['input'])
        self.assertIn(quote, second['input'])
        self.assertIn(reasoning, third['input'])
        outputs = [x for x in third['input'] if x.get('type') == 'function_call_output']
        self.assertEqual([x['call_id'] for x in outputs], ['call-1', 'call-2'])
        self.assertEqual(json.loads(outputs[0]['output'])['request_id'], 'template-1')
        self.assertEqual([r['work_id'] for r in client.requests], ['work-1', 'work-1', 'work-2'])
        self.assertEqual(
            Conversation('new run', {}).items, [dict(role='system', content='new run')]
        )

    def test_plain_text_does_not_finish_and_no_trade_is_required(self):
        text = dict(
            type='message', role='assistant', content=[dict(type='output_text', text='No trade.')]
        )
        client = ScriptClient([dict(output=[text]), dict(output=[call('control.finish', 1)])])
        ConfiguredAgentProgram()(client, template_configuration())
        self.assertEqual([r['tool'] for r in client.requests], ['control.finish'])
        self.assertIn(text, client.payloads[1]['input'])

    def test_submit_can_be_followed_by_reads_errors_and_wait(self):
        buy = dict(symbol='AAPL', side='buy', quantity_shares=1, limit_price_usd='100.0000')
        client = ScriptClient(
            [
                dict(output=[call('orders.submit_limit', 1, buy)]),
                dict(output=[call('orders.get', 2, dict(order_id='missing'))]),
                dict(output=[call('account.snapshot', 3)]),
                dict(
                    output=[
                        call('control.wait', 4, dict(conditions=[dict(kind='after', seconds='1')]))
                    ]
                ),
            ]
        )
        ConfiguredAgentProgram()(client, template_configuration())
        self.assertEqual(
            [r['tool'] for r in client.requests],
            ['orders.submit_limit', 'orders.get', 'account.snapshot', 'control.wait'],
        )
        error = json.loads(client.payloads[2]['input'][-1]['output'])
        self.assertEqual(error['error']['code'], 'ORDER_NOT_FOUND')

    def test_bad_tool_input_is_returned_to_model_without_effects(self):
        client = ScriptClient(
            [
                dict(output=[call('unknown', 1)]),
                dict(output=[call('market.quote', 2, dict(symbol='OTHER'))]),
                dict(output=[call('control.finish', 3)]),
            ]
        )
        ConfiguredAgentProgram()(client, template_configuration())
        self.assertEqual([r['tool'] for r in client.requests], ['control.finish'])
        self.assertEqual(
            json.loads(client.payloads[1]['input'][-1]['output'])['error']['code'],
            'TOOL_UNAVAILABLE',
        )

    def test_mixed_finish_batch_is_rejected_before_any_trade(self):
        buy = dict(symbol='AAPL', side='buy', quantity_shares=1, limit_price_usd='100.0000')
        client = ScriptClient(
            [
                dict(output=[call('orders.submit_limit', 1, buy), call('control.finish', 2)]),
                dict(output=[call('control.finish', 3)]),
            ]
        )
        ConfiguredAgentProgram()(client, template_configuration())
        self.assertEqual([r['tool'] for r in client.requests], ['control.finish'])
        results = [
            x for x in client.payloads[1]['input'] if x.get('type') == 'function_call_output'
        ]
        self.assertEqual(len(results), 2)
        self.assertTrue(all(json.loads(x['output'])['status'] == 'error' for x in results))

    def test_malformed_response_and_duplicate_ids_fail_before_any_tool(self):
        conversation = Conversation('prompt', {})
        valid = call('control.finish', 1)
        for value in ({}, dict(output=[valid, valid]), dict(output=[dict(type='function_call')])):
            with self.assertRaisesRegex(ProviderError, 'FORMAT_ERROR'):
                validate_response(value, conversation.call_ids)
            self.assertEqual(len(conversation.items), 1)
        conversation.accept_response([valid])
        with self.assertRaisesRegex(ProviderError, 'FORMAT_ERROR'):
            validate_response(dict(output=[valid]), conversation.call_ids)

    def test_context_failure_stops_work_without_summary_or_trade_retry(self):
        client = ScriptClient([ProviderError('CONTEXT_TOO_LONG')])
        ConfiguredAgentProgram()(client, template_configuration())
        self.assertEqual(client.failures, ['CONTEXT_TOO_LONG'])
        self.assertEqual(client.requests, [])
        with self.assertRaisesRegex(ProviderError, 'CONTEXT_TOO_LONG'):
            validate_input(dict(input='x' * 65536))


class TemplateIntegrationTests(AgentExperimentFixture, unittest.TestCase):

    def test_real_process_loop_crosses_tools_and_keeps_context_at_next_interval(self):
        experiment = self.open(
            [
                dict(output=[call('market.quote', 1, dict(symbol='AAPL'))]),
                dict(output=[call('account.snapshot', 2)]),
                dict(output=[call('control.finish', 3)]),
                dict(
                    output=[
                        call('control.wait', 4, dict(conditions=[dict(kind='after', seconds='30')]))
                    ]
                ),
            ],
            interval='1',
        )
        experiment.play()
        first, second, third = self.reply(), self.reply(), self.reply()
        self.assertNotEqual(first['pid'], os.getpid())
        self.assertEqual(first['pid'], experiment.runner.process_host.snapshot()['pid'])
        quote = json.loads(second['payload']['input'][-1]['output'])
        self.assertEqual(quote['status'], 'ok')
        self.assertEqual(quote['data']['symbol'], 'AAPL')
        self.assertIn('cutoff', quote)
        self.assertEqual(json.loads(third['payload']['input'][-1]['output'])['status'], 'ok')
        self.idle()
        self.assertFalse(self.incoming.poll(0.05))
        fourth = self.reply()
        self.assertIn(
            dict(role='system', content='Use tools to evaluate opportunities.'),
            fourth['payload']['input'],
        )
        outputs = [x for x in fourth['payload']['input'] if x.get('type') == 'function_call_output']
        self.assertEqual([x['call_id'] for x in outputs], ['call-1', 'call-2', 'call-3'])
        finish_due = int(json.loads(outputs[-1]['output'])['data']['next_due_ns'])
        work_message = fourth['payload']['input'][-1]['content']
        next_work = json.loads(work_message.removeprefix('Work started: '))
        self.assertGreaterEqual(int(next_work['dispatched_ns']), finish_due)
        until(lambda: experiment.runner.snapshot()['work']['state'] == 'waiting')
        experiment.stop()
        archive = self.app.agent_log_archive(self.profile, experiment.run_id)
        tools = [r['request']['tool'] for r in archive['records'] if r['kind'] == 'tool']
        self.assertEqual(
            tools, ['market.quote', 'account.snapshot', 'control.finish', 'control.wait']
        )
        self.assertEqual(self.app.snapshot(self.profile, self.account).orders, ())

    def test_event_schedule_waits_for_visible_market_event(self):
        experiment = self.open([dict(output=[call('control.finish', 1)])], events=True)
        experiment.play()
        self.assertFalse(self.incoming.poll(0.1))
        self.reply()
        self.idle()
        experiment.stop()

    def test_continuous_finish_and_explicit_wait_both_use_runtime_schedule(self):
        experiment = self.open(
            [
                dict(output=[call('control.finish', 1)]),
                dict(
                    output=[
                        call(
                            'control.wait', 2, dict(conditions=[dict(kind='after', seconds='0.05')])
                        )
                    ]
                ),
                dict(
                    output=[
                        call('control.wait', 3, dict(conditions=[dict(kind='after', seconds='30')]))
                    ]
                ),
            ],
            interval=None,
        )
        experiment.play()
        self.reply()
        second, third = self.reply(), self.reply()
        second_work = json.loads(
            second['payload']['input'][-1]['content'].removeprefix('Work started: ')
        )
        third_work = json.loads(
            third['payload']['input'][-1]['content'].removeprefix('Work started: ')
        )
        finish_output = next(
            x for x in second['payload']['input'] if x.get('type') == 'function_call_output'
        )
        self.assertEqual(
            second_work['scheduled_due_ns'],
            json.loads(finish_output['output'])['data']['next_due_ns'],
        )
        self.assertEqual(third_work['reason'], 'wait')
        self.assertNotIn('work_id', third_work)
        until(lambda: experiment.runner.snapshot()['work'].get('wait_id') == 'wait-3')
        experiment.stop()

    def test_pause_holds_reply_and_stop_prevents_late_trade(self):
        buy = dict(symbol='AAPL', side='buy', quantity_shares=1, limit_price_usd='200.0000')
        experiment = self.open([dict(output=[call('orders.submit_limit', 1, buy)])], delay=0.2)
        experiment.play()
        self.reply()
        experiment.pause()
        frozen = experiment.runner.pipeline.clock.target_ns()
        time.sleep(0.3)
        self.assertEqual(experiment.runner.pipeline.clock.target_ns(), frozen)
        self.assertEqual(self.app.snapshot(self.profile, self.account).orders, ())
        self.assertFalse(self.incoming.poll())
        experiment.stop()
        self.assertEqual(self.app.snapshot(self.profile, self.account).orders, ())

    def test_resume_uses_retained_reply_once_and_can_finish_without_trading(self):
        experiment = self.open([dict(output=[call('control.finish', 1)])], delay=0.2)
        experiment.play()
        self.reply()
        experiment.pause()
        time.sleep(0.3)
        self.assertNotEqual(experiment.runner.snapshot()['work']['state'], 'idle')
        experiment.play()
        self.idle()
        self.assertFalse(self.incoming.poll(0.05))
        experiment.stop()
        archive = self.app.agent_log_archive(self.profile, experiment.run_id)
        tools = [r['request']['tool'] for r in archive['records'] if r['kind'] == 'tool']
        self.assertEqual(tools, ['control.finish'])

    def test_format_retry_occurs_before_tools_and_keeps_the_same_input(self):
        experiment = self.open(
            [dict(output=[dict(type='function_call')]), dict(output=[call('control.finish', 1)])]
        )
        experiment.play()
        first, second = self.reply(), self.reply()
        self.assertEqual(first['payload'], second['payload'])
        self.idle()
        experiment.stop()
        archive = self.app.agent_log_archive(self.profile, experiment.run_id)
        tools = [r for r in archive['records'] if r['kind'] == 'tool']
        self.assertEqual(len(tools), 1)

    def test_context_error_is_named_and_never_retried(self):
        experiment = self.open(['CONTEXT_TOO_LONG'])
        experiment.play()
        self.reply()
        until(lambda: experiment.runner.snapshot()['work']['state'] == 'failed')
        self.assertEqual(experiment.runner.snapshot()['work']['failure'], 'CONTEXT_TOO_LONG')
        self.assertFalse(self.incoming.poll(0.1))
        experiment.stop()

    def test_trade_then_model_retry_then_read_does_not_resubmit_order(self):
        self.source.write_bytes(
            prepared.directory('AAPL', timestamp=1)
            + prepared.add(1, 'B', 20, 99, 'AAPL', timestamp=2)
            + prepared.add(2, 'S', 20, 101, 'AAPL', timestamp=3)
            + prepared.add(3, 'B', 1, 98, 'AAPL', timestamp=500_000_000)
            + prepared.trade(1, 100, 2, 60_000_000_000)
            + b'\0\0'
        )
        buy = dict(symbol='AAPL', side='buy', quantity_shares=1, limit_price_usd='0.0101')
        clock = TestClock(3, FakeTime())
        experiment = self.open(
            [
                dict(output=[call('orders.submit_limit', 1, buy)]),
                'RATE_LIMIT',
                dict(output=[call('account.snapshot', 2)]),
                dict(output=[call('control.finish', 3)]),
            ],
            clock=clock,
            delay=0.6,  # Even the first model response exceeds the old 500 ms race window.
        )
        experiment.play()

        def advance_boundary():
            clock.advance_to(clock.target_ns() + 1)

        replies = []
        for _ in range(4):
            until(self.incoming.poll, advance=advance_boundary)
            replies.append(self.reply())
        first, retry, accepted, after_read = replies
        self.assertEqual(retry['payload'], accepted['payload'])
        submitted = json.loads(retry['payload']['input'][-1]['output'])
        self.assertEqual(submitted['status'], 'ok')
        self.assertEqual(submitted['data']['outcome'], 'accepted')
        account_reply = json.loads(after_read['payload']['input'][-1]['output'])
        self.assertEqual(account_reply['status'], 'ok')
        self.assertTrue(first['payload']['input'][-1]['content'].startswith('Work started: '))
        until(
            lambda: experiment.runner.snapshot()['work']['state'] == 'idle',
            advance=advance_boundary,
        )
        # The order is accepted before the only matching market update, on any runner.
        self.assertEqual(self.app.snapshot(self.profile, self.account).orders[0].filled, 0)
        clock.advance_to(500_000_001)
        until(lambda: self.app.snapshot(self.profile, self.account).orders[0].filled == 1)
        experiment.stop()
        account = self.app.snapshot(self.profile, self.account)
        self.assertEqual(len(account.orders), 1)
        self.assertEqual(account.orders[0].quantity, 1)
        self.assertEqual(account.orders[0].filled, 1)

    def test_agent_context_failure_keeps_accepted_order_until_experiment_stop(self):
        buy = dict(symbol='AAPL', side='buy', quantity_shares=1, limit_price_usd='0.0001')
        experiment = self.open(
            [dict(output=[call('orders.submit_limit', 1, buy)]), 'CONTEXT_TOO_LONG']
        )
        experiment.play()
        self.reply()
        self.reply()
        until(lambda: experiment.runner.snapshot()['work']['state'] == 'failed')
        account = self.app.snapshot(self.profile, self.account)
        self.assertEqual(len(account.orders), 1)
        self.assertEqual(account.orders[0].status, 'accepted')
        experiment.stop()
        self.assertEqual(
            self.app.snapshot(self.profile, self.account).orders[0].status, 'cancelled'
        )

    def test_eof_stops_template_and_discards_unreturned_model_action(self):
        buy = dict(symbol='AAPL', side='buy', quantity_shares=1, limit_price_usd='200.0000')
        clock = TestClock(3, FakeTime())
        experiment = self.open(
            [dict(output=[call('orders.submit_limit', 1, buy)])],
            delay=2,
            end_ns='300000000',
            clock=clock,
        )
        experiment.play()
        self.reply()
        clock.advance_to(300_000_001)
        until(lambda: experiment.runner.pipeline.snapshot()['state'] == 'completed')
        until(lambda: experiment.runner.output_finished)
        self.assertEqual(self.app.snapshot(self.profile, self.account).orders, ())
        self.assertEqual(self.app.run(self.profile, experiment.run_id)['status'], 'completed')

    def test_model_binding_rejects_incomplete_template_grants(self):
        with self.assertRaisesRegex(ContractError, 'control.finish'):
            self.open([], grants=['market.quote', 'model.request'])
