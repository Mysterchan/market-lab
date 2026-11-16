"""Python Agent import, environment and original-operation Replay through the new UI."""

import json
import os
import sys
import unittest
from support.desktop import DesktopFixture


@unittest.skipUnless(os.environ.get('TRADING_DESKTOP_TESTS') == '1', 'optional Desktop suite')
class DesktopAgentTests(DesktopFixture, unittest.TestCase):
    def run_python_agent(self, mode):
        project = self.path / mode
        project.mkdir()
        (project / 'agent.json').write_text(
            json.dumps(dict(python=f'{sys.version_info.major}.{sys.version_info.minor}'))
        )
        signature = ', task' if mode == 'python_strategy' else ''
        source = (
            'class Agent:\n'
            '    def __init__(self, context): self.context=context\n'
            f'    def run(self{signature}):\n'
        )
        if mode == 'python_carrier':
            source += '        self.context.next_task("Read a quote")\n'
        source += (
            '        self.context.tool("market.quote").run(symbol="AAPL")\n'
            '        self.context.tool("control.finish").run()\n'
            'def create_agent(context): return Agent(context)\n'
        )
        (project / 'agent.py').write_text(source)
        self.click('[data-action="agents"]')
        self.click('[data-action="agent-new"]')
        self.js(
            "document.querySelector('[name=integration]').value=" + json.dumps(mode) + ";"
            "document.querySelector('[name=integration]').dispatchEvent(new Event('input',{bubbles:true}));"
            "document.querySelector('[name=integration]').dispatchEvent(new Event('change',{bubbles:true}))"
        )
        self.js(
            "document.querySelector('[name=name]').value='Python reader';"
            "document.querySelector('[name=prompt]').value='Read a quote and finish';"
            "document.querySelector('[name=requires]').value='market.quote,control.finish';"
            "document.querySelector('[name=source]').value=" + json.dumps(str(project)) + ";"
            "document.querySelector('#agent-form').requestSubmit()"
        )
        self.until(lambda: bool(self.window.agent_catalogue.get('agents')))
        saved = self.window.agent_catalogue['agents'][0]
        self.until(lambda: self.js('document.querySelectorAll(".agent-card").length') == 1)
        self.click('[data-action="environment:' + saved['agent_id'] + '"]')
        self.js("document.querySelector('#environment-form').requestSubmit()")
        self.until(lambda: self.window.agent_catalogue['agents'][0]['environment'] == 'Ready', 40)
        self.until(
            lambda: self.js(
                'document.querySelectorAll("[data-action=\\"agent-use:'
                + saved['agent_id']
                + '\\"]").length'
            )
            > 0
        )
        self.click('[data-action="agent-use:' + saved['agent_id'] + '"]')
        self.js("document.querySelector('#setup-form').requestSubmit()")
        self.js("document.querySelector('#setup-form').requestSubmit()")
        self.js("document.querySelector('#setup-form').requestSubmit()")
        self.until(lambda: any(v.get('mode') == 'agent' for v in self.window.views.values()), 30)
        identity = next(k for k, v in self.window.views.items() if v.get('mode') == 'agent')
        self.until(lambda: self.js('document.querySelectorAll("[data-action=play]").length') > 0)
        self.click('[data-action="play"]')
        self.until(
            lambda: bool(self.window.views[identity].get('activity'))
            or self.window.views[identity]['pipeline']['state'] == 'ended'
        )
        self.window.dispatch('session', dict(session=identity, method='pause'))
        self.until(lambda: self.window.views[identity]['pipeline']['state'] in ('paused', 'ended'))
        self.window.dispatch('session', dict(session=identity, method='stop'))
        self.until(lambda: self.window.views[identity]['execution']['status'] != 'running')
        log = next(
            (self.path / 'accounts').rglob(self.window.views[identity]['run']['id'] + '.jsonl')
        )
        results = []

        def received(encoded):
            event = json.loads(encoded)
            if event['kind'] == 'replay':
                results.append(event['value'])

        self.window.browser.commands.event.connect(received)
        self.window.dispatch('prepare_replay', dict(path=str(log)))
        self.until(lambda: bool(results), 30)
        self.assertTrue(results[0]['matched'])
        self.assertTrue(results[0]['events_verified'])
        self.assertEqual(results[0]['model_calls'], 0)
        self.assertEqual(self.errors, [])

    def test_reference_agent_is_chosen_prepared_and_rehearsed_to_a_trade(self):
        from unittest.mock import patch
        from sim_platform import Platform
        from sim_platform.models.providers.rehearsal import RehearsalProvider

        self.click('[data-action="agents"]')
        self.click('[data-action="agent-new"]')
        self.js(
            "const i=document.querySelector('[name=integration]');i.value='python_strategy';"
            "i.dispatchEvent(new Event('input',{bubbles:true}));"
            "i.dispatchEvent(new Event('change',{bubbles:true}))"
        )
        self.until(lambda: self.js('document.querySelectorAll("[name=reference]").length') == 1)
        self.js(
            "const r=document.querySelector('[name=reference]');r.value='trading_agents';"
            "r.dispatchEvent(new Event('input',{bubbles:true}));"
            "r.dispatchEvent(new Event('change',{bubbles:true}))"
        )
        self.js(
            "document.querySelector('[name=model]').value='rehearsal';"
            "document.querySelector('[name=prompt]').value='Decide with the team';"
            "document.querySelector('#agent-form').requestSubmit()"
        )
        self.until(lambda: bool(self.window.agent_catalogue.get('agents')), 30)
        saved = self.window.agent_catalogue['agents'][0]
        self.assertEqual(saved['definition']['name'], 'Multi-role trading firm (TradingAgents)')
        self.assertIn('news.list', saved['definition']['requires'])
        self.until(lambda: self.js('document.querySelectorAll(".agent-card").length') == 1)
        self.click('[data-action="environment:' + saved['agent_id'] + '"]')
        self.js("document.querySelector('#environment-form').requestSubmit()")
        self.until(lambda: self.window.agent_catalogue['agents'][0]['environment'] == 'Ready', 60)
        self.window.dispatch(
            'save_credential',
            dict(reference='desktop-model', secret='local-test-key', persistence='session'),
        )
        original = Platform.configure_experiment_model
        with patch.object(
            Platform,
            'configure_experiment_model',
            lambda app, controller, profile: original(
                app, controller, profile, provider=RehearsalProvider('AAPL')
            ),
        ):
            self.until(
                lambda: self.js(
                    'document.querySelectorAll("[data-action=\\"agent-use:'
                    + saved['agent_id']
                    + '\\"]").length'
                )
                > 0
            )
            self.click('[data-action="agent-use:' + saved['agent_id'] + '"]')
            self.js("document.querySelector('#setup-form').requestSubmit()")
            self.until(
                lambda: self.js('document.querySelectorAll("[name=agent_pacing]").length') == 2
            )
            self.js(
                "document.querySelector('[name=agent_pacing][value=turn]').click();"
                "document.querySelector('[name=end_time]').value='09:30:06';"
                "document.querySelector('#setup-form').requestSubmit()"
            )
            self.until(lambda: 'Turn-based' in (self.js('document.body.textContent') or ''))
            self.js("document.querySelector('#setup-form').requestSubmit()")
            self.until(
                lambda: any(v.get('mode') == 'agent' for v in self.window.views.values()), 30
            )
        identity = next(k for k, v in self.window.views.items() if v.get('mode') == 'agent')
        self.window.dispatch('session', dict(session=identity, method='play', speed=1))
        self.until(lambda: self.window.views[identity]['execution']['status'] == 'completed', 60)
        run_id = self.window.views[identity]['run']['id']
        reader = Platform(self.path / 'accounts')
        self.addCleanup(reader.close)
        profile = reader.profiles()[0]['id']
        events = reader.run_events(profile, run_id)
        self.assertTrue([e for e in events if e['kind'] == 'fill'], [e['kind'] for e in events])
        notes = reader.agent_journal(profile, run_id)
        self.assertTrue(any(n['text'].startswith('Risk manager:') for n in notes))
        log = next((self.path / 'accounts').rglob(run_id + '.jsonl'))
        results = []

        def received(encoded):
            event = json.loads(encoded)
            if event['kind'] == 'replay':
                results.append(event['value'])

        self.window.browser.commands.event.connect(received)
        self.window.dispatch('prepare_replay', dict(path=str(log)))
        self.until(lambda: bool(results), 30)
        self.assertTrue(results[0]['events_verified'])
        self.assertEqual(results[0]['model_calls'], 0)
        self.assertEqual(self.errors, [])

    def test_python_strategy_round_trip(self):
        self.run_python_agent('python_strategy')

    def test_python_carrier_round_trip(self):
        self.run_python_agent('python_carrier')
