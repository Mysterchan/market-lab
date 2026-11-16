"""Local OpenAI-compatible model servers: profile checks, translation and a full Agent run."""

import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import time
import unittest

from sim_platform import ContractError, Platform
from sim_platform.examples.configured_agent_demo import example_configuration
from sim_platform.examples.market_demo import write_fixture
from sim_platform.models.contracts import ModelProfile, ProviderError, local_server
from sim_platform.models.providers.local import (
    OpenAICompatibleChat,
    chat_messages,
    default_provider,
)
from sim_platform.models.providers.openai import OpenAIResponses
from sim_platform.models.providers.rehearsal import RehearsalProvider

GRANTS = ['market.quote', 'control.finish', 'model.request', 'orders.submit_market']


class ChatServer:
    """A Chat Completions server on 127.0.0.1 that answers like RehearsalProvider.

    It omits tool call IDs and sends arguments as objects, as some local servers do.
    """

    def __init__(self, symbol='AAPL'):
        self.requests = []
        self.rehearsal = RehearsalProvider(symbol)
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                server.requests.append(
                    dict(path=self.path, body=body, auth=self.headers.get('Authorization'))
                )
                reply = json.dumps(server.answer(body)).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(reply)))
                self.end_headers()
                self.wfile.write(reply)

        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.httpd.server_address[1]}/v1'

    def answer(self, body):
        used = [
            dict(type='function_call', name=call['function']['name'])
            for message in body['messages']
            for call in message.get('tool_calls') or []
        ]
        tools = [dict(name=tool['function']['name']) for tool in body.get('tools') or []]
        call = self.rehearsal.choose(dict(tools=tools, input=used))
        return dict(
            model='local-test-7b',
            choices=[
                dict(
                    message=dict(
                        role='assistant',
                        content='',
                        tool_calls=[
                            dict(
                                type='function',
                                function=dict(
                                    name=call['name'], arguments=json.loads(call['arguments'])
                                ),
                            )
                        ],
                    )
                )
            ],
            usage=dict(prompt_tokens=100, completion_tokens=10, total_tokens=110),
        )

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class ProfileTests(unittest.TestCase):
    def test_a_local_profile_needs_a_server_on_premises(self):
        for url in (
            'http://127.0.0.1:11434/v1',
            'http://localhost:8000/v1',
            'https://192.168.1.20/v1',
            'http://10.0.0.5:8080',
            'http://[::1]:8000/v1',
        ):
            ModelProfile('openai-compatible', 'qwen', base_url=url)
        for url in (
            'http://api.openai.com/v1',
            'http://8.8.8.8/v1',
            'ftp://127.0.0.1/v1',
            'http://127.0.0.1/v1?key=1',
            'http://user@127.0.0.1/v1',
        ):
            with self.assertRaises(ContractError, msg=url):
                ModelProfile('openai-compatible', 'qwen', base_url=url)
        with self.assertRaises(ContractError):
            ModelProfile('openai-compatible', 'qwen')
        with self.assertRaises(ContractError):
            ModelProfile('openai-responses', 'gpt', 'key', base_url='http://127.0.0.1/v1')
        with self.assertRaises(ContractError):
            ModelProfile('openai-responses', 'gpt')  # the hosted API needs a key

    def test_server_address_and_default_path(self):
        self.assertEqual(
            local_server('http://localhost:8000/v1/'), (False, 'localhost', 8000, '/v1')
        )
        self.assertEqual(local_server('https://10.1.2.3'), (True, '10.1.2.3', 443, '/v1'))

    def test_the_default_adapter_follows_the_provider(self):
        local = ModelProfile('openai-compatible', 'qwen', base_url='http://127.0.0.1/v1')
        self.assertIsInstance(default_provider(local), OpenAICompatibleChat)
        hosted = ModelProfile('openai-responses', 'gpt', 'key')
        self.assertIsInstance(default_provider(hosted), OpenAIResponses)


class TranslationTests(unittest.TestCase):
    def test_responses_items_become_chat_messages(self):
        payload = dict(
            instructions='Be careful.',
            input=[
                dict(role='system', content='Prompt'),
                dict(role='user', content='Task: trade'),
                dict(type='reasoning', summary=[]),
                dict(type='function_call', call_id='a', name='market_quote', arguments='{}'),
                dict(type='function_call', call_id='b', name='account_snapshot', arguments='{}'),
                dict(type='function_call_output', call_id='a', output='{"bid": 1}'),
                dict(type='function_call_output', call_id='b', output='{"cash": 2}'),
                dict(
                    type='message',
                    role='assistant',
                    content=[dict(type='output_text', text='Looked.')],
                ),
            ],
        )
        messages = chat_messages(payload)
        self.assertEqual(
            [m['role'] for m in messages],
            ['system', 'system', 'user', 'assistant', 'tool', 'tool', 'assistant'],
        )
        self.assertEqual([c['id'] for c in messages[3]['tool_calls']], ['a', 'b'])
        self.assertEqual(messages[4], dict(role='tool', tool_call_id='a', content='{"bid": 1}'))
        self.assertEqual(messages[6]['content'], 'Looked.')

    def test_replies_get_unique_call_ids_and_responses_usage(self):
        adapter = OpenAICompatibleChat()
        call = dict(function=dict(name='control_finish', arguments={}))
        data = dict(
            model='m',
            choices=[
                dict(
                    message=dict(
                        content='Done.', tool_calls=[call, dict(call, id='x'), dict(call, id='x')]
                    )
                )
            ],
            usage=dict(prompt_tokens=5, completion_tokens=2),
        )
        reply = adapter.reply(data)
        kinds = [item['type'] for item in reply['output']]
        self.assertEqual(kinds, ['message', 'function_call', 'function_call', 'function_call'])
        ids = [item['call_id'] for item in reply['output'][1:]]
        self.assertEqual(len(set(ids)), 3)
        self.assertEqual(reply['output'][1]['arguments'], '{}')
        self.assertEqual(reply['usage'], dict(input_tokens=5, output_tokens=2))
        self.assertEqual(reply['model'], 'm')
        # An ID seen in an earlier reply is replaced too.
        again = adapter.reply(dict(choices=[dict(message=dict(tool_calls=[dict(call, id='x')]))]))
        self.assertNotIn(again['output'][0]['call_id'], ids)
        with self.assertRaises(ProviderError):
            adapter.reply(dict(choices=[]))

    def test_one_request_reaches_the_local_server_without_a_key(self):
        server = ChatServer()
        self.addCleanup(server.close)
        profile = ModelProfile(
            'openai-compatible', 'local-test', base_url=server.url, temperature=0
        )
        payload = dict(
            input='Hello', tools=[dict(type='function', name='control_finish', parameters={})]
        )
        reply = asyncio.run(OpenAICompatibleChat().request(profile, payload, ''))
        self.assertEqual(reply['output'][0]['name'], 'control_finish')
        sent = server.requests[0]
        self.assertEqual(sent['path'], '/v1/chat/completions')
        self.assertIsNone(sent['auth'])
        self.assertEqual(sent['body']['temperature'], 0)
        self.assertEqual(sent['body']['model'], 'local-test')
        reply = asyncio.run(OpenAICompatibleChat().request(profile, payload, 'token'))
        self.assertEqual(server.requests[1]['auth'], 'Bearer token')

    def test_a_closed_port_is_a_transport_error(self):
        server = ChatServer()
        url = server.url
        server.close()
        profile = ModelProfile('openai-compatible', 'x', base_url=url, network_timeout_seconds=2)
        with self.assertRaises(ProviderError) as caught:
            asyncio.run(OpenAICompatibleChat().request(profile, dict(input='x'), ''))
        self.assertEqual(caught.exception.code, 'TRANSPORT_ERROR')


class LocalAgentTests(unittest.TestCase):
    def test_an_agent_trades_with_a_local_model_and_records_it(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        server = ChatServer('AAPL')
        self.addCleanup(server.close)
        root = Path(temp.name)
        source, _ = write_fixture(root)
        definition, settings = example_configuration()
        definition['requires'] = settings['granted_tools'] = GRANTS
        settings = dict(settings, speed='turn')
        app = Platform(root / 'accounts')
        self.addCleanup(app.close)
        profile = app.create_profile('Research')
        app.create_agent(profile, definition)
        account = app.create_account(profile, 'Cash', 100_000_000)
        experiment = app.open_experiment(
            profile,
            account,
            source=source,
            method='agent',
            agent_id=definition['agent_id'],
            revision=1,
            settings=settings,
            execution_mode='live',
        )
        self.addCleanup(experiment.close)
        # No stored key and no provider object: the profile selects the local adapter.
        app.configure_experiment_model(
            experiment, ModelProfile('openai-compatible', 'local-test', base_url=server.url)
        )
        started = time.monotonic()
        experiment.play()
        while experiment.runner.active or not experiment.runner.archive_ready:
            experiment.tick(render=False)
            time.sleep(0.01)
            self.assertLess(time.monotonic() - started, 60, 'experiment did not finish')
        experiment.tick(render=False)
        report = experiment.report()
        archive = app.agent_log_archive(profile, experiment.run_id)
        self.assertEqual(report['run']['status'], 'completed')
        self.assertGreaterEqual(report['summary']['fill_count'], 1)
        self.assertTrue(server.requests)
        notices = [r for r in archive['records'] if r.get('event') == 'model_profile']
        self.assertEqual(notices[0]['details']['provider'], 'openai-compatible')
        self.assertEqual(notices[0]['details']['base_url'], server.url)


if __name__ == '__main__':
    unittest.main()
