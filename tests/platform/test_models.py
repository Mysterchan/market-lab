"""Agent-local model contracts, provider classification and credential isolation."""

import asyncio
import json
import unittest
from sim_platform.domain.models import ContractError
from sim_platform.models.contracts import ModelProfile, ProviderError, validate_input
from sim_platform.models.credentials import CredentialStore
from sim_platform.models.providers.openai import decode_response, read_body
from support.models import AgentModelFixture, ChildModelProvider


class BrokenStore:
    def put(self, *args):
        raise ContractError('OS write failed')

    def get(self, *args):
        raise ContractError('OS read failed')

    def delete(self, *args):
        raise ContractError('OS delete failed')


class MemoryStore:
    """Stands in for the OS credential store; a missing entry reads as None."""

    def __init__(self, values):
        self.values = dict(values)

    def put(self, reference, secret):
        self.values[reference] = secret

    def get(self, reference):
        return self.values.get(reference)

    def delete(self, reference):
        self.values.pop(reference, None)


class RedactingProvider(ChildModelProvider):
    async def request_received(self, profile, value, secret, received):
        result = await super().request_received(profile, value, secret, received)
        return dict(result, text='contains ' + secret)


class ModelTests(unittest.TestCase):
    setUp = AgentModelFixture.setUp
    open_model = AgentModelFixture.open_model

    def test_success_and_redaction_stay_in_agent(self):
        _, experiment, incoming = self.open_model(RedactingProvider())
        experiment.play()
        self.assertTrue(incoming.poll(3))
        reply = incoming.recv()
        self.assertIsNone(reply['error'])
        self.assertEqual(reply['result']['text'], 'contains [redacted]')
        experiment.stop()

    def test_context_and_unsupported_model_errors_are_fatal(self):
        for code in ('CONTEXT_TOO_LONG', 'MODEL_UNSUPPORTED'):
            with self.subTest(code=code):
                _, experiment, incoming = self.open_model(
                    ChildModelProvider(errors=[code]), timeout='0.5'
                )
                experiment.play()
                self.assertTrue(incoming.poll(3))
                self.assertEqual(incoming.recv()['error'], code)
                experiment.stop()

    def test_payload_and_profile_bounds(self):
        for value in (dict(input='x' * 65536), dict(input='hi', key='secret'), dict(input=None)):
            with self.assertRaises(ContractError):
                validate_input(value)
        with self.assertRaises(ContractError):
            ModelProfile('other', 'model', 'ref')
        with self.assertRaises(ContractError):
            ModelProfile('openai-responses', 'model', 'ref', network_timeout_seconds=float('nan'))
        for temperature in (-0.1, 2.5, float('nan'), '0.5', True):
            with self.assertRaises(ContractError):
                ModelProfile('openai-responses', 'model', 'ref', temperature=temperature)
        self.assertEqual(ModelProfile('openai-responses', 'm', 'ref', temperature=0).temperature, 0)

    def test_credentials_fail_without_plaintext_fallback(self):
        keys = CredentialStore(BrokenStore())
        for action in (
            lambda: keys.put('test', 'secret', persistence='os'),
            lambda: keys.get('test'),
            lambda: keys.delete('test'),
        ):
            with self.assertRaises(ContractError):
                action()
        self.assertEqual(keys._session, {})
        keys.put('test', 'secret')
        keys.delete('test')
        with self.assertRaises(ContractError):
            keys.get('test')
        with self.assertRaises(ContractError):
            CredentialStore().put('test', 'secret', persistence='os')

    def test_saved_os_credential_is_found_and_a_missing_one_is_reported(self):
        keys = CredentialStore(MemoryStore({'saved': 'stored-key'}))
        self.assertEqual(keys.get('saved'), 'stored-key')
        with self.assertRaisesRegex(ContractError, 'credential not configured'):
            keys.get('absent')

    def test_desktop_load_credential_confirms_a_saved_os_credential(self):
        from tempfile import TemporaryDirectory
        from types import SimpleNamespace
        from sim_platform import Platform
        from sim_platform.desktop.tasks import DesktopTasks

        with TemporaryDirectory() as directory, Platform(directory) as app:
            app._credentials = CredentialStore(MemoryStore({'saved': 'stored-key'}))
            messages = []
            worker = SimpleNamespace(
                runtime=SimpleNamespace(platform=app, job=SimpleNamespace(active=False)),
                message=SimpleNamespace(emit=messages.append),
            )
            tasks = DesktopTasks(worker)
            tasks.execute('load_credential', dict(reference='saved'))
            self.assertEqual(len(messages), 1)
            self.assertNotIn('stored-key', messages[0])
            with self.assertRaisesRegex(ContractError, 'credential not configured'):
                tasks.execute('load_credential', dict(reference='absent'))
            self.assertEqual(len(messages), 1)

    def test_provider_status_body_and_retry_header_classification(self):
        for status, body, code in [
            (401, {}, 'AUTH_FAILED'),
            (429, {}, 'RATE_LIMIT'),
            (503, {}, 'PROVIDER_TEMPORARY'),
            (400, dict(error=dict(code='context_length_exceeded')), 'CONTEXT_TOO_LONG'),
        ]:
            with self.assertRaisesRegex(ProviderError, code):
                decode_response(status, {'retry-after': '3'}, json.dumps(body).encode())
        with self.assertRaisesRegex(ProviderError, 'FORMAT_ERROR'):
            decode_response(200, {}, b'{')
        with self.assertRaisesRegex(ProviderError, 'FORMAT_ERROR'):
            decode_response(200, {}, b'{"status":"incomplete","output":[]}')
        self.assertEqual(
            decode_response(200, {}, b'{"status":"completed","output":[]}')['output'], []
        )

    def test_provider_chunked_truncation_and_response_bounds(self):
        async def check(data, headers):
            reader = asyncio.StreamReader()
            reader.feed_data(data)
            reader.feed_eof()
            return await read_body(reader, headers)

        self.assertEqual(
            asyncio.run(check(b'3\r\nabc\r\n0\r\n\r\n', {'transfer-encoding': 'chunked'})), b'abc'
        )
        with self.assertRaises(asyncio.IncompleteReadError):
            asyncio.run(check(b'ab', {'content-length': '3'}))
        with self.assertRaises(ProviderError):
            asyncio.run(check(b'', {'content-length': '65537'}))
