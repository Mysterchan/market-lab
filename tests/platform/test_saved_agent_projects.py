"""Saved source → selected interpreter → existing experiment/Runtime/tools, without factories."""

import json
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
import zipfile

from sim_platform import Platform, ContractError
from sim_platform.agents.hosting.lifecycle import AgentStartupError
from sim_platform.sdk.distribution import FILENAME
from support.agents import settings
from support.clock import until
from support import prepared_market as prepared

SOURCE = '''import json, os, sys
from pathlib import Path
import sim_platform.sdk as sdk
from helper import double

class Agent:
    def __init__(self, context):
        self.context = context
        local = Path('local-state.txt')
        self.count = int(local.read_text()) if local.exists() else 0
        local.write_text('99')
        self.path = Path(context.settings['parameters']['report'])
        self.path.write_text(json.dumps(dict(phase='ready', count=0, pid=os.getpid(),
            python=sys.version.split()[0], sdk=sdk.__file__, local=double(3), working_directory=os.getcwd())))

    def run(self, task):
        self.count += 1
        quote = self.context.tool('market.quote').run('AAPL')
        if self.context.settings['parameters'].get('trade'):
            self.context.tool('orders.submit_limit').run('AAPL', 'buy', 1, '0.0010')
        self.context.tool('control.finish').run()
        self.path.write_text(json.dumps(dict(phase='worked', count=self.count,
            pid=os.getpid(), python=sys.version.split()[0], sdk=sdk.__file__, quote=quote)))

def create_agent(context):
    return Agent(context)
'''
RESIDENT = (
    SOURCE[: SOURCE.index('def create_agent(context):')]
    + '''class Resident(Agent):
    def run(self):
        while not self.context.stopping:
            task = self.context.next_task('Evaluate one opportunity')
            super().run(task)

def create_agent(context):
    return Resident(context)
'''
)


class SavedAgentTests(unittest.TestCase):
    def setUp(self):
        prepared.PreparedMarketFixture.setUp(self)
        self.app = Platform(self.root / 'app')
        self.addCleanup(self.app.close)
        self.profile = self.app.create_profile('Saved agent')
        self.account = self.app.create_account(self.profile, 'Cash', 10000000)
        self.project = self.root / 'project'
        self.project.mkdir()
        self.python = os.environ.get(
            'AGENT_TEST_PYTHON', f'{sys.version_info.major}.{sys.version_info.minor}'
        )
        (self.project / 'agent.json').write_text(json.dumps(dict(python=self.python)))
        (self.project / 'helper.py').write_text('def double(value): return value * 2\n')

    def save(self, source=SOURCE, *, resident=False, **options):
        (self.project / 'agent.py').write_text(source)
        report = self.root / ('report-' + str(len(self.app.agents(self.profile))) + '.json')
        saved = self.app.import_agent(
            self.profile,
            self.project,
            integration='python_carrier' if resident else 'python_strategy',
            parameters=dict(report=str(report), trade=True),
            requires=options.pop(
                'requires', ['market.quote', 'orders.submit_limit', 'control.finish']
            ),
            **options,
        )
        return saved, report

    def prepare(self, saved):
        job = self.app.prepare_agent_environment(
            self.profile,
            saved['agent_id'],
            python_archive=(
                os.environ.get('AGENT_TEST_RUNTIME_ARCHIVE')
                if not self.app.python_runtimes()
                else None
            ),
        )
        try:
            state = job.wait(60)
            self.assertEqual(state['state'], 'ready', state)
            return state
        finally:
            job.close()

    def open(self, saved, *, research=False):
        options = dict(
            source=self.source,
            method='agent',
            agent_id=saved['agent_id'],
            revision=saved['revision'],
            execution_mode='live',
            settings=dict(
                settings(),
                start_ns='3',
                end_ns='50000000000',
                granted_tools=saved['definition']['requires'],
            ),
        )
        if research:
            experiment = self.app.open_research(method='agent')
            self.addCleanup(experiment.close)
            experiment.create_experiment(self.profile, self.account, **options)
        else:
            experiment = self.app.open_experiment(self.profile, self.account, **options)
            self.addCleanup(experiment.close)
        return experiment

    def read(self, report):
        try:
            return json.loads(report.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return dict(phase="writing")

    def verify_replay(self, experiment):
        target = self.app.create_account(self.profile, 'Replay', 10000000)
        with patch(
            'sim_platform.agents.hosting.host.AgentProcessHost',
            side_effect=AssertionError('Replay must not load the saved project'),
        ):
            result = self.app.replay_prepared_operations(
                self.profile, target, experiment.runner._recorder.path
            )
        self.assertTrue(result['events_verified'])
        self.assertEqual(result['model_calls'], 0)
        self.assertEqual([event['kind'] for event in result['events']], ['accepted', 'cancelled'])

    def test_saved_scheduled_agent_uses_installed_sdk_and_real_tool_trade_path(self):
        saved, report = self.save()
        ready = self.prepare(saved)
        (self.project / 'agent.py').write_text('raise RuntimeError("original source changed")')
        experiment = self.open(saved)
        initialized = self.read(report)
        self.assertEqual(initialized['phase'], 'ready')
        self.assertEqual(initialized['local'], 6)
        self.assertTrue(initialized['python'].startswith(self.python))
        self.assertIn('site-packages', initialized['sdk'])
        self.assertNotEqual(initialized['pid'], os.getpid())
        self.assertEqual(
            experiment.runner.snapshot()['agent_process']['executable'],
            str(self.app._agent_environment_files.executable(ready['environment'])),
        )
        experiment.play()
        until(lambda: report.exists() and self.read(report)['phase'] == 'worked')
        self.assertEqual(self.read(report)['quote']['status'], 'ok')
        experiment.pause()
        experiment.play()
        experiment.stop()
        self.assertFalse(Path(initialized['working_directory']).exists())
        self.assertEqual(len(self.app.snapshot(self.profile, self.account).orders), 1)
        self.assertEqual(
            self.app.snapshot(self.profile, self.account).orders[0].status, 'cancelled'
        )
        self.verify_replay(experiment)

    def test_resident_project_uses_the_same_research_entry_and_runtime(self):
        saved, report = self.save(RESIDENT, resident=True)
        self.prepare(saved)
        experiment = self.open(saved, research=True)
        experiment.play()
        until(lambda: self.read(report)['phase'] == 'worked')
        experiment.stop()
        self.assertEqual(self.read(report)['count'], 1)
        self.verify_replay(experiment)

    def test_repeated_experiments_get_fresh_agent_state_with_reusable_environment(self):
        saved, report = self.save()
        self.prepare(saved)
        first = self.open(saved)
        pid = self.read(report)['pid']
        first.play()
        until(lambda: self.read(report)['phase'] == 'worked')
        first.stop()
        second_account = self.app.create_account(self.profile, 'Second account', 10000000)
        self.account = second_account
        second = self.open(saved)
        self.assertEqual(self.read(report)['count'], 0)
        self.assertNotEqual(self.read(report)['pid'], pid)
        second.play()
        until(lambda: self.read(report)['phase'] == 'worked')
        second.stop()
        self.assertEqual(self.read(report)['count'], 1)
        self.assertTrue(self.prepare(saved)['reused'])

    def test_unprepared_environment_fails_before_children_and_compensates_run(self):
        saved, report = self.save()
        with self.assertRaisesRegex(ContractError, 'environment is not ready'):
            self.open(saved)
        self.assertFalse(report.exists())
        self.assertEqual(self.app.runs(self.profile)[0]['status'], 'stopped')
        self.assertEqual(self.app._resources._roots, {})

    def test_initialization_import_failure_cleans_run_and_owned_resources(self):
        source = 'import missing_project_dependency\ndef create_agent(context): return None\n'
        saved, _ = self.save(source)
        self.prepare(saved)
        with self.assertRaises(AgentStartupError) as raised:
            self.open(saved)
        self.assertEqual(raised.exception.code, 'AGENT_DEPENDENCY_MISSING')
        self.assertEqual(self.app.runs(self.profile)[0]['status'], 'stopped')
        self.assertEqual(self.app._resources._roots, {})

    def test_installed_agent_model_runs_in_child_and_reports_attempts_to_runtime(self):
        provider_source = """from pathlib import Path
import os
class Provider:
    def __init__(self, marker): self.marker = marker
    async def request(self, profile, payload, secret):
        Path(self.marker).write_text(str(os.getpid()))
        return dict(output=[dict(type='function_call', call_id='finish-1', name='control_finish', arguments='{}')])
"""
        provider_path = self.project / 'saved_provider.py'
        provider_path.write_text(provider_source)
        specification = importlib.util.spec_from_file_location('saved_provider', provider_path)
        module = importlib.util.module_from_spec(specification)
        sys.modules['saved_provider'] = module
        self.addCleanup(lambda: sys.modules.pop('saved_provider', None))
        specification.loader.exec_module(module)
        source = """import json
from pathlib import Path
from sim_platform.sdk import TradingAgent

def create_agent(context):
    inner = TradingAgent(context.model, context.tools.values(), 'Finish without a trade.')
    class Agent:
        def run(self, task):
            inner.run(task)
            Path(context.settings['parameters']['report']).write_text(json.dumps(dict(phase='worked')))
    return Agent()
"""
        saved, report = self.save(source, requires=['model.request', 'control.finish'])
        self.prepare(saved)
        experiment = self.open(saved)
        marker = self.root / 'model-pid'
        self.app.set_model_credential('fixture', 'fixture-secret')
        from sim_platform.models.contracts import ModelProfile

        self.app.configure_experiment_model(
            experiment,
            ModelProfile('openai-responses', 'fixture', 'fixture'),
            provider=module.Provider(str(marker)),
        )
        experiment.play()
        until(lambda: self.read(report)['phase'] == 'worked')
        self.assertEqual(
            int(marker.read_text()), experiment.runner.snapshot()['agent_process']['pid']
        )
        experiment.stop()
        archive = self.app.agent_log_archive(self.profile, experiment.run_id)
        self.assertNotIn('fixture-secret', json.dumps(archive))

    def test_installed_sdk_wait_validation_does_not_import_the_scheduler_owner(self):
        source = SOURCE.replace(
            "self.context.tool('control.finish').run()",
            "self.context.tool('control.wait').run([dict(kind='after', seconds='30')])",
        )
        saved, report = self.save(
            source, requires=['market.quote', 'orders.submit_limit', 'control.wait']
        )
        self.prepare(saved)
        experiment = self.open(saved)
        experiment.play()
        until(lambda: self.read(report)['phase'] == 'worked')
        experiment.stop()

    def test_failed_runtime_setup_removes_the_new_execution_source_copy(self):
        saved, _ = self.save()
        self.prepare(saved)
        with patch.object(
            self.app, '_new_experiment_runtime', side_effect=RuntimeError('runtime unavailable')
        ):
            with self.assertRaisesRegex(RuntimeError, 'runtime unavailable'):
                self.open(saved)
        self.assertEqual(list(self.app._workspace.path.glob('agent-*')), [])
        self.assertEqual(self.app._resources._roots, {})

    def test_sdk_export_imports_without_repo_orm_or_native_packages(self):
        saved, _ = self.save()
        ready = self.prepare(saved)
        wheel = self.app.export_agent_sdk(self.root / FILENAME)
        self.assertTrue(Path(wheel['path']).is_file())
        with zipfile.ZipFile(wheel['path']) as archive:
            self.assertFalse(
                any(
                    name.startswith(
                        (
                            'sim_platform/application/',
                            'sim_platform/persistence/',
                            'sim_platform/market/',
                            'sim_platform/desktop/',
                        )
                    )
                    for name in archive.namelist()
                )
            )
        executable = self.app._agent_environment_files.executable(ready['environment'])
        program = '''import sys
from sim_platform.sdk import AgentContext, TradingAgent, Tool
assert 'sqlalchemy' not in sys.modules
assert not any('sim_platform.application' in n for n in sys.modules)
print(AgentContext.__module__)
'''
        result = subprocess.check_output(
            [str(executable), '-I', '-c', program], cwd=self.root, text=True
        )
        self.assertIn('sim_platform.sdk.context', result)


if __name__ == '__main__':
    unittest.main()
