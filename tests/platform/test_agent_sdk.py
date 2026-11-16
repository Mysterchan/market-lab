"""Public Agent factories, local tools and preparation boundaries through real spawn."""

import json
import multiprocessing
import os
import time
import unittest

from sim_platform import ContractError
from sim_platform.sdk import Tool, ToolError, TradingAgent
from sim_platform.agents.carrier.program import ConfiguredAgentProgram
from sim_platform.agents.carrier.model import ToolCall
from sim_platform.agents.hosting.host import AgentProcessHost
from sim_platform.agents.hosting.protocol import TransportLimits
from examples.agents.math_agent import PercentageChange, create_agent as math_agent
from examples.agents.resident_agent import create_agent as resident_agent
from support.carrier import (
    ScriptClient,
    template_configuration,
    call,
)
from support.carrier import AgentExperimentFixture
from sim_platform.agents.carrier.conversation import Conversation
from support.clock import until


class SDKLicenseTests(unittest.TestCase):
    def test_source_change_refreshes_same_named_development_wheel(self):
        from tempfile import TemporaryDirectory
        from unittest.mock import patch
        import zipfile
        from sim_platform.persistence.agents.sdk import SDKFiles, wheel_contents

        with TemporaryDirectory() as directory:
            files = SDKFiles(directory)
            wheel = files.wheel()
            changed = dict(wheel_contents())
            changed['sim_platform/sdk/context.py'] += b'\n# development source changed\n'
            with patch('sim_platform.persistence.agents.sdk.wheel_contents', return_value=changed):
                self.assertEqual(files.wheel(), wheel)
            with zipfile.ZipFile(wheel) as archive:
                self.assertEqual(
                    archive.read('sim_platform/sdk/context.py'),
                    changed['sim_platform/sdk/context.py'],
                )
                self.assertTrue(
                    any(name.endswith('/licenses/NOTICE') for name in archive.namelist())
                )

    def test_generated_and_old_cached_wheel_keep_project_and_upstream_notices(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path
        import zipfile
        from sim_platform.persistence.agents.sdk import SDKFiles
        from sim_platform.sdk.distribution import FILENAME

        with TemporaryDirectory() as directory:
            files = SDKFiles(directory)
            files.root.mkdir(parents=True)
            cached = files.root / FILENAME
            with zipfile.ZipFile(cached, 'w') as archive:
                archive.writestr('legacy-cache.txt', 'wheel created before license selection')
            wheel = files.wheel()
            with zipfile.ZipFile(wheel) as archive:
                names = archive.namelist()
                for name in ('Apache-2.0.txt', 'NOTICE', 'Python-PSF.txt'):
                    matches = [entry for entry in names if entry.endswith('/licenses/' + name)]
                    self.assertEqual(len(matches), 1)
                    self.assertTrue(archive.read(matches[0]))
                self.assertNotIn('legacy-cache.txt', names)
                metadata = next(entry for entry in names if entry.endswith('/METADATA'))
                self.assertIn(b'License: Apache-2.0', archive.read(metadata))
            self.assertEqual(files.wheel(), wheel)
            self.assertTrue(Path(wheel).is_file())


class ObservedFactory:
    def __init__(self, messages, *, invalid=False, delay=0, use_capability=False):
        self.messages = messages
        self.invalid, self.delay, self.use_capability = invalid, delay, use_capability

    def __call__(self, context):
        self.messages.put(dict(kind='created', pid=os.getpid()))
        if self.use_capability:
            context.next_task('Not allowed during initialization')
        if self.delay:
            time.sleep(self.delay)
        if self.invalid:
            return object()
        return ObservedAgent(context, self.messages)


class ObservedAgent:
    def __init__(self, context, messages):
        self.context, self.messages = context, messages

    def run(self, task):
        self.messages.put(dict(kind='running', pid=os.getpid()))
        self.context.tool('control.finish').run()


class LocalToolTests(unittest.TestCase):
    def test_local_numeric_output_reaches_model_without_platform_request(self):
        client = ScriptClient(
            [
                dict(output=[call('percentage_change', 1, {'before': 100, 'after': 110})]),
                dict(output=[call('control.finish', 2)]),
            ]
        )
        ConfiguredAgentProgram(math_agent)(client, template_configuration())
        self.assertAlmostEqual(json.loads(client.payloads[1]['input'][-1]['output']), 10)
        self.assertEqual([r['tool'] for r in client.requests], ['control.finish'])

    def test_local_errors_and_finite_result_validation(self):
        tool = PercentageChange()
        for args, code in [
            ({'before': 0, 'after': 1}, 'TOOL_EXECUTION_FAILED'),
            ({'before': 1}, 'INVALID_SCHEMA'),
            ({'before': 1, 'after': float('inf')}, 'TOOL_RESULT_INVALID'),
        ]:
            with self.assertRaises(ToolError) as error:
                tool.invoke(args)
            self.assertEqual(error.exception.code, code)

    def test_local_limit_is_not_replaced_by_platform_page_limit(self):
        class Local(Tool):
            name, description = 'local', 'Local calculation'
            parameters = {'limit': {'type': 'integer', 'maximum': 1000}}

            def run(self, limit):
                return limit

        self.assertEqual(
            Local().input_schema({'page_items': 1})['properties']['limit']['maximum'], 1000
        )

    def test_unknown_and_reserved_tools(self):
        agent = TradingAgent(None, [], 'test')
        result, done = agent._execute(ToolCall('1', 'missing', '{}'))
        self.assertEqual(result['error']['code'], 'TOOL_UNAVAILABLE')
        self.assertFalse(done)

        class Impersonator(PercentageChange):
            name = 'market_quote'

        with self.assertRaisesRegex(ContractError, 'replace platform'):
            TradingAgent(None, [Impersonator()], 'test')

    def test_preparation_rejects_capabilities_and_keeps_instances_separate(self):
        observed = []

        def factory(context):
            with self.assertRaisesRegex(ContractError, 'AGENT_INITIALIZING'):
                context.next_task('x')
            with self.assertRaisesRegex(ContractError, 'AGENT_INITIALIZING'):
                context.model.respond(Conversation('test', {}), [])
            with self.assertRaisesRegex(ContractError, 'AGENT_INITIALIZING'):
                context.tool('market.quote').run('AAPL')
            with self.assertRaisesRegex(ToolError, 'not granted or provided'):
                context.tool('unknown')
            observed.append(context)
            return math_agent(context)

        program = ConfiguredAgentProgram(factory)
        first = program.prepare(ScriptClient([]), template_configuration())
        second = program.prepare(ScriptClient([]), template_configuration())
        self.assertIsNot(first.agent, second.agent)
        self.assertIsNot(first.agent.conversation, second.agent.conversation)
        settings = observed[0].settings
        settings['parameters'].clear()
        self.assertTrue(observed[0].settings['parameters'])


class FactoryProcessTests(unittest.TestCase):
    def test_agent_exists_before_play_and_no_decision_starts_early(self):
        messages = multiprocessing.get_context('spawn').Queue()
        self.addCleanup(messages.close)
        host = AgentProcessHost(
            lambda request: None,
            template_configuration(),
            TransportLimits.from_configuration(template_configuration()['template']['limits']),
            agent_factory=ObservedFactory(messages),
        )
        self.addCleanup(host.close)
        created = messages.get(timeout=3)
        self.assertEqual(created['kind'], 'created')
        self.assertNotEqual(created['pid'], os.getpid())
        self.assertTrue(messages.empty())
        self.assertEqual(host.snapshot()['state'], 'ready')

    def test_failed_initialization_and_timeout_reap_child(self):
        for options in ({'invalid': True}, {'use_capability': True}, {'delay': 2}):
            messages = multiprocessing.get_context('spawn').Queue()
            try:
                with self.assertRaises(ContractError):
                    AgentProcessHost(
                        lambda request: None,
                        template_configuration(),
                        TransportLimits.from_configuration(
                            template_configuration()['template']['limits']
                        ),
                        agent_factory=ObservedFactory(messages, **options),
                        startup_timeout=0.5,
                    )
                children = [
                    p for p in multiprocessing.active_children() if p.name == 'agent-program'
                ]
                self.assertEqual(children, [])
            finally:
                messages.close()


class PublicFactoryIntegrationTests(unittest.TestCase):
    setUp = AgentExperimentFixture.setUp
    open = AgentExperimentFixture.open
    reply = AgentExperimentFixture.reply

    def test_external_math_agent_uses_local_tool_then_platform_ipc(self):
        experiment = self.open(
            [
                dict(output=[call('percentage_change', 1, {'before': 100, 'after': 110})]),
                dict(output=[call('market.quote', 2, {'symbol': 'AAPL'})]),
                dict(output=[call('control.finish', 3)]),
            ],
            agent_factory=math_agent,
        )
        experiment.play()
        first, second, third = self.reply(), self.reply(), self.reply()
        self.assertNotEqual(first['pid'], os.getpid())
        self.assertAlmostEqual(json.loads(second['payload']['input'][-1]['output']), 10)
        self.assertEqual(json.loads(third['payload']['input'][-1]['output'])['status'], 'ok')
        until(lambda: experiment.runner.snapshot()['work']['state'] == 'idle')

    def test_external_resident_agent_replaces_template(self):
        experiment = self.open([], agent_factory=resident_agent, integration='python_carrier')
        experiment.play()
        # A resident carrier immediately starts its next round. Its transient
        # idle state is not a completion signal that a polling test can rely on.
        until(lambda: experiment.runner.process_host.snapshot()['transport']['delivered'] >= 2)
        self.assertFalse(self.incoming.poll(0.05), 'resident example does not call a model')
        self.assertEqual(experiment.runner.process_host.snapshot()['state'], 'running')
        experiment.stop()
        archive = self.app.agent_log_archive(self.profile, experiment.run_id)
        tools = [record for record in archive['records'] if record['kind'] == 'tool']
        self.assertEqual(
            [record['request']['tool'] for record in tools[:2]],
            ['market.quote', 'control.finish'],
        )
        self.assertEqual([record['result']['status'] for record in tools[:2]], ['ok', 'ok'])
        self.assertEqual(tools[1]['result']['data']['work_state'], 'idle')
        self.assertIsNotNone(tools[1]['result']['data']['next_due_ns'])
