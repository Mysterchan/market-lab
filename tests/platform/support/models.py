"""Real Agent-local model requests with scripted provider behavior."""

import asyncio
import time
from unittest.mock import patch
from support.prepared_market import PreparedMarketFixture


class ChildModelProvider:
    def __init__(self, errors=(), delay=0, parse_delay=0):
        self.errors = list(errors)
        self.delay = delay
        self.parse_delay = parse_delay
        self.calls = 0

    async def request_received(self, profile, value, secret, received):
        import asyncio
        import os
        from sim_platform.models.contracts import ProviderError

        self.calls += 1
        await asyncio.sleep(self.delay)
        received()
        # Deliberately block Agent parsing after complete receipt.
        if self.parse_delay:
            time.sleep(self.parse_delay)
        if self.errors:
            code = self.errors.pop(0)
            if code:
                raise ProviderError(code)
        return dict(
            pid=os.getpid(),
            credential_selected=secret == 'fixture-secret',
            calls=self.calls,
            output=[],
        )


class ChildModelProgram:
    def __init__(self, outgoing):
        self.outgoing = outgoing

    def __call__(self, client, configuration):
        try:
            work = client.next_work()
            result = client.request_model(work, {'input': 'hello'})
            self.outgoing.send(dict(result=result, error=None))
        except Exception as error:
            self.outgoing.send(dict(result=None, error=str(error)))
        finally:
            self.outgoing.close()
        client.wait_stopped()


class AgentModelFixture(PreparedMarketFixture):
    def open_model(self, provider, timeout='0.1'):
        import multiprocessing
        from functools import partial
        from sim_platform import Platform
        from support.agents import definition, settings
        from sim_platform.agents.hosting.host import AgentProcessHost
        from sim_platform.models.contracts import ModelProfile

        self._model_number = getattr(self, '_model_number', 0) + 1
        app = Platform(self.root / ('model-app-' + str(self._model_number)))
        self.addCleanup(app.close)
        profile = app.create_profile('model')
        account = app.create_account(profile, 'cash', 10000)
        app.create_agent(profile, definition())
        config = dict(settings(), start_ns='3', end_ns='50000000000', timeout_seconds=timeout)
        incoming, outgoing = multiprocessing.get_context('spawn').Pipe(duplex=False)
        self.addCleanup(incoming.close)
        with patch(
            'sim_platform.agents.hosting.host.AgentProcessHost',
            partial(AgentProcessHost, program=ChildModelProgram(outgoing)),
        ):
            experiment = app.open_experiment(
                profile,
                account,
                source=self.source,
                method='agent',
                agent_id='demo',
                revision=1,
                settings=config,
                execution_mode='live',
            )
        outgoing.close()
        app.set_model_credential('fixture', 'fixture-secret')
        app.configure_experiment_model(
            experiment, ModelProfile('openai-responses', 'mock', 'fixture'), provider=provider
        )
        return app, experiment, incoming


class MockProvider:
    def __init__(self, replies, delay=0):
        self.replies, self.delay, self.calls = list(replies), delay, 0

    async def request(self, profile, payload, secret):
        self.calls += 1
        await asyncio.sleep(self.delay)
        value = self.replies[min(self.calls - 1, len(self.replies) - 1)]
        if isinstance(value, Exception):
            raise value
        return value


class BuyThenFinishProvider:
    """A configured-template model that buys 5 AAPL at market, then finishes its work."""

    def __init__(self):
        self.calls = 0

    async def request(self, profile, payload, secret):
        self.calls += 1
        if self.calls == 1:
            name = 'orders_submit_market'
            arguments = '{"symbol":"AAPL","side":"buy","quantity_shares":5}'
        else:
            name, arguments = 'control_finish', '{}'
        call = dict(type='function_call', call_id=f'c{self.calls}', name=name, arguments=arguments)
        usage = dict(input_tokens=100, output_tokens=20, total_tokens=120)
        return dict(output=[call], usage=usage)


class SlowBuyThenFinishProvider(BuyThenFinishProvider):
    """BuyThenFinishProvider whose every reply takes ``delay`` seconds of real time."""

    def __init__(self, delay=0.5):
        super().__init__()
        self.delay = delay

    async def request(self, profile, payload, secret):
        await asyncio.sleep(self.delay)
        return await super().request(profile, payload, secret)


class QuoteThenFinishProvider:
    """Each work round reads a quote, then finishes: a minimal Agent turn."""

    def __init__(self):
        self.calls = 0

    async def request(self, profile, payload, secret):
        self.calls += 1
        quote = self.calls % 2 == 1
        name = 'market_quote' if quote else 'control_finish'
        arguments = '{"symbol":"AAPL"}' if quote else '{}'
        call = dict(type='function_call', call_id=f'q{self.calls}', name=name, arguments=arguments)
        return dict(output=[call], usage=dict(input_tokens=10, output_tokens=2))
