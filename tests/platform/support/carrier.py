"""Scripted providers and shared real-process Agent experiment fixtures."""

import asyncio
import copy
import json
import multiprocessing
import os
from sim_platform import Platform, ContractError
from sim_platform.agents.tools.protocol.contracts import IMPLEMENTED_TOOLS
from sim_platform.models.contracts import ModelProfile, ProviderError
from support.agents import definition, settings
from support.clock import until
from support import prepared_market as prepared
from support.prepared_market import PreparedMarketFixture


def call(name, number, arguments=None):
    return dict(
        type='function_call',
        call_id='call-' + str(number),
        name=name.replace('.', '_'),
        arguments=json.dumps(arguments or {}),
        status='completed',
    )


def template_configuration():
    return dict(
        run_id='run-1',
        template=dict(
            initial_prompt='Original user system prompt.',
            parameters={'risk_preference': 'user choice'},
            granted_tools=sorted(IMPLEMENTED_TOOLS | {'model.request'}),
            limits=settings()['limits'],
            symbol='AAPL',
        ),
    )


class ScriptClient:
    def __init__(self, replies, rounds=1):
        self.replies = iter(replies)
        self.rounds = rounds
        self.number = 0
        self.payloads, self.requests, self.failures = [], [], []
        self.work_client = self
        self.stopping = False

    def prepare_model(self):
        pass

    def wait_running(self):
        pass

    def wait_stopped(self):
        self.stopping = True

    def fail(self, code):
        self.failures.append(code)

    def next_work(self):
        self.number += 1
        if self.number > self.rounds:
            self.stopping = True
            raise ContractError('stopped')
        return dict(work_id='work-' + str(self.number), epoch='1', notifications=[])

    def request_model(self, work, payload, *, validate_response=None):
        self.payloads.append(copy.deepcopy(payload))
        result = next(self.replies)
        if isinstance(result, Exception):
            raise result
        validate_response(result)
        return result

    def request_tool(self, request):
        self.requests.append(copy.deepcopy(request))
        if request['tool'] == 'orders.get':
            return dict(status='error', error=dict(code='ORDER_NOT_FOUND'))
        return dict(status='ok', request_id=request['request_id'], data={'source': 'fixture'})


class RecordingProvider:
    """Child-local scripted model; pipe records what the real template submitted."""

    def __init__(self, connection, replies, delay=0):
        self.connection, self.replies, self.delay = connection, replies, delay
        self.number = 0

    async def request(self, profile, payload, secret):
        self.connection.send(dict(pid=os.getpid(), number=self.number, payload=payload))
        await asyncio.sleep(self.delay)
        value = self.replies[min(self.number, len(self.replies) - 1)]
        self.number += 1
        if isinstance(value, str):
            raise ProviderError(value)
        return copy.deepcopy(value)


class AgentExperimentFixture(PreparedMarketFixture):
    def open(
        self,
        replies,
        *,
        delay=0,
        interval='30',
        events=False,
        grants=None,
        end_ns='50000000000',
        agent_factory=None,
        integration='configured',
        clock=None,
    ):
        self.app = Platform(self.root / 'app')
        self.addCleanup(self.app.close)
        self.profile = self.app.create_profile('Template')
        self.account = self.app.create_account(self.profile, 'Cash', 10000000)
        agent = definition()
        agent['schedule']['interval_seconds'] = interval
        if interval is None:
            agent['schedule'] = dict(
                mode='continuous', busy_event_policy='preserve_interval', events=[]
            )
        if events:
            agent['schedule'] = dict(
                mode='event',
                busy_event_policy='preserve_interval',
                events=[dict(kind='market_update', symbol='AAPL')],
            )
        if integration != 'configured':
            agent['integration'] = integration
            agent['package'] = dict(project_ref='fixture-project')
            if integration == 'python_carrier':
                agent['carrier'] = 'custom'
                agent.pop('schedule')
        self.app.create_agent(self.profile, agent)
        if events:
            self.source.write_bytes(
                prepared.directory('AAPL', timestamp=1)
                + prepared.add(1, 'B', 20, 99, 'AAPL', timestamp=2)
                + prepared.add(2, 'S', 20, 101, 'AAPL', timestamp=3)
                + prepared.trade(1, 100, 2, 500_000_000)
                + prepared.trade(2, 100, 2, 60_000_000_000)
                + b'\0\0'
            )
        config = dict(
            settings(),
            start_ns='3',
            end_ns=end_ns,
            granted_tools=(
                sorted(IMPLEMENTED_TOOLS | {'model.request'}) if grants is None else grants
            ),
        )
        config['retry'].update(base_delay_seconds='0', max_delay_seconds='0')
        self.experiment = self.app.open_experiment(
            self.profile,
            self.account,
            source=self.source,
            method='agent',
            agent_id='demo',
            revision=1,
            settings=config,
            execution_mode='live',
            agent_factory=agent_factory,
            clock=clock,
        )
        self.incoming, outgoing = multiprocessing.get_context('spawn').Pipe(duplex=False)
        self.addCleanup(self.incoming.close)
        self.app.set_model_credential('fixture', 'fixture-secret')
        # Keep this end open until the test ends: on Windows the Agent process duplicates
        # the pipe handle from this process when it unpacks the provider, which can happen
        # after configure_experiment_model() returns.
        self.addCleanup(outgoing.close)
        self.app.configure_experiment_model(
            self.experiment,
            ModelProfile('openai-responses', 'mock', 'fixture'),
            provider=RecordingProvider(outgoing, replies, delay),
        )
        return self.experiment

    def reply(self):
        self.assertTrue(self.incoming.poll(4), self.experiment.runner.snapshot())
        return self.incoming.recv()

    def idle(self):
        until(lambda: self.experiment.runner.snapshot()['work']['state'] == 'idle')
