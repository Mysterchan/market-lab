"""Reference Agents: catalogue, static project checks, import, and one decision each."""

import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from sim_platform import ContractError, Platform
from sim_platform.agents.carrier.model import ToolCall
from sim_platform.agents.tools.client.collection import TOOL_TYPES
from sim_platform.reference_agents import CATALOGUE, reference_agent, reference_agents
from sim_platform.sdk import AgentContext, Task

DAY_NS = 86_400 * 1_000_000_000


class FakeClient:
    """Answers platform tools with canned data and records every call."""

    def __init__(self):
        self.calls = []

    def call_tool(self, name, arguments):
        self.calls.append(name)
        data = dict(items=[dict(headline='Acme beats estimates', available_time_ns=str(DAY_NS))])
        return dict(
            status='ok', cutoff=dict(market_position=dict(time_ns=str(2 * DAY_NS))), data=data
        )


class ScriptedModel:
    """Ends every role at once: reports, lessons and beliefs are submitted, traders finish."""

    def __init__(self):
        self.tool_sets = []
        self.count = 0

    def respond(self, conversation, tools):
        names = {tool.function_name for tool in tools}
        self.tool_sets.append(names)
        self.count += 1
        for name, arguments in (
            ('report_submit', dict(report='Looks steady.')),
            ('memory_store', dict(lesson='Earnings news moved the price.', importance=0.8)),
            ('beliefs_update', dict(beliefs='Favour quality, size small.')),
            ('control_finish', {}),
        ):
            if name in names:
                return [ToolCall(f'call-{self.count}', name, json.dumps(arguments))]
        raise AssertionError(f'no ending tool among {sorted(names)}')


def load(key):
    path = Path(reference_agent(key)['path']) / 'agent.py'
    spec = importlib.util.spec_from_file_location(f'reference_{key}', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def context(key, model, client):
    entry = reference_agent(key)
    tools = [TOOL_TYPES[name](client) for name in entry['requires'] if name in TOOL_TYPES]
    settings = dict(
        initial_prompt='Trade carefully.',
        parameters=dict(entry['parameters']),
        granted_tools=list(entry['requires']),
        limits={},
        symbol='ACME',
    )
    return AgentContext(None, model, tools, settings)


class CatalogueTests(unittest.TestCase):
    def test_every_entry_has_a_project_and_known_tools(self):
        from sim_platform.agents.configuration import AVAILABLE_TOOLS

        self.assertEqual({e['key'] for e in reference_agents()}, set(CATALOGUE))
        for entry in reference_agents():
            self.assertTrue((Path(entry['path']) / 'agent.py').is_file())
            self.assertTrue(entry['url'].startswith('https://arxiv.org/abs/'))
            self.assertLessEqual(set(entry['requires']), AVAILABLE_TOOLS)
        with self.assertRaises(ContractError):
            reference_agent('missing')

    def test_projects_pass_the_import_check_and_save(self):
        with TemporaryDirectory() as temp:
            with Platform(Path(temp) / 'data') as app:
                profile = app.create_profile('Research')
                listed = app.reference_agents()
                self.assertNotIn('path', listed[0])
                for entry in listed:
                    checked = app.check_agent_project(reference_agent(entry['key'])['path'])
                    self.assertTrue(checked)
                saved = app.import_reference_agent(profile, 'trading_agents')
                definition = saved['definition']
                self.assertEqual(definition['name'], CATALOGUE['trading_agents']['name'])
                self.assertIn('fundamentals.get', definition['requires'])
                self.assertEqual(definition['parameters'], {'debate_rounds': 1})
                path = reference_agent('trading_agents')['path']
                app.import_agent(profile, path, agent_id=saved['agent_id'], expected_revision=1)
                run = dict(run=dict(config=dict(agent=dict(definition=saved['definition']))))
                rule = dict(run=dict(config={}))
                # Two saved revisions: the Agent entry counts two trials, the rule none.
                self.assertEqual(
                    app._revision_trials(profile, {'Agent': [run, run], 'Rule': [rule]}),
                    {'Agent': 2},
                )


class DesktopFormTests(unittest.TestCase):
    def test_reference_choice_fills_source_tools_and_parameters(self):
        from sim_platform.desktop.workflows.agents import with_reference_agent

        form = dict(
            reference='layered_memory',
            source='',
            name='',
            requires=['market.quote', 'control.finish'],
            parameters={'risk': 'averse'},
            integration='python_strategy',
        )
        filled = with_reference_agent(form)
        self.assertEqual(filled['source'], reference_agent('layered_memory')['path'])
        self.assertEqual(filled['name'], CATALOGUE['layered_memory']['name'])
        self.assertEqual(filled['parameters'], {'risk': 'averse', 'memories': 8})
        self.assertEqual(filled['requires'][:2], ['market.quote', 'control.finish'])
        self.assertIn('news.list', filled['requires'])
        own = with_reference_agent(dict(form, source='/my/agent'))
        self.assertEqual(own['source'], '/my/agent')

    def test_unknown_finish_tool_is_refused(self):
        from sim_platform.sdk import TradingAgent

        with self.assertRaises(ContractError):
            TradingAgent(ScriptedModel(), [], 'x', finish_tools=('report.submit',))


class DecisionTests(unittest.TestCase):
    def test_trading_firm_consults_each_role_with_only_its_tools(self):
        model, client = ScriptedModel(), FakeClient()
        agent = load('trading_agents').create_agent(context('trading_agents', model, client))
        result = agent.run(Task('Decide for today.'))
        self.assertEqual(result['status'], 'ok')
        # Three analysts, bull, bear, risk manager, trader.
        self.assertEqual(model.count, 7)
        analyst_tools = model.tool_sets[:3]
        self.assertIn('news_list', analyst_tools[1])
        self.assertNotIn('orders_submit_limit', analyst_tools[0] | analyst_tools[1])
        self.assertIn('orders_submit_limit', model.tool_sets[-1])

    def test_memory_trader_recalls_news_and_stores_a_lesson(self):
        model, client = ScriptedModel(), FakeClient()
        agent = load('layered_memory').create_agent(context('layered_memory', model, client))
        agent.run(Task('Decide for today.'))
        self.assertEqual(agent.memory.items[-1]['layer'], 'short')  # no reflection yet
        agent.run(Task('Decide again.'))  # reflects on the first decision first
        self.assertIn('news.list', client.calls)
        layers = [m['layer'] for m in agent.memory.items]
        self.assertEqual(layers[-1], 'deep')
        recalled = agent.memory.recall('earnings news', 2 * DAY_NS, 2)
        self.assertTrue(any('Acme' in m['text'] or 'Earnings' in m['text'] for m in recalled))

    def test_manager_updates_beliefs_after_deciding(self):
        model, client = ScriptedModel(), FakeClient()
        agent = load('manager_analyst').create_agent(context('manager_analyst', model, client))
        agent.run(Task('Decide for today.'))
        self.assertIn('No beliefs yet', agent.beliefs)
        agent.run(Task('Decide again.'))  # critiques the first decision first
        self.assertEqual(agent.beliefs, 'Favour quality, size small.')


if __name__ == '__main__':
    unittest.main()
