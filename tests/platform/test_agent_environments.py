"""Real offline wheel preparation, reuse, cancellation and failure cleanup."""

import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import Mock, patch
import zipfile
from sim_platform import Platform
from sim_platform.agents.preparation import process as commands
from sim_platform.agents.preparation.process import run_command
from sim_platform.agents.preparation.errors import PreparationCancelled

SOURCE = 'def create_agent(context): return None\n'


def wheel(folder, name='tiny_dependency', version='1.0', *, hook=False, dependency=None):
    dist = f'{name}-{version}.dist-info'
    path = folder / f'{name}-{version}-py3-none-any.whl'
    files = {
        name + '.py': 'raise RuntimeError("must not import installed packages")\n',
        dist
        + '/METADATA': f'Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n'
        + (f'Requires-Dist: {dependency}\n' if dependency else ''),
        dist
        + '/WHEEL': 'Wheel-Version: 1.0\nGenerator: fixture\nRoot-Is-Purelib: true\nTag: py3-none-any\n',
    }
    if hook:
        files['startup.pth'] = 'import os; raise RuntimeError("unsafe")\n'
    files[dist + '/RECORD'] = ''.join(name + ',,\n' for name in [*files, dist + '/RECORD'])
    with zipfile.ZipFile(path, 'w') as archive:
        for name, text in files.items():
            archive.writestr(name, text)
    return path


class AgentEnvironmentTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.app = Platform(self.root / 'app')
        self.addCleanup(self.app.close)
        self.profile = self.app.create_profile('Owner')
        self.project = self.root / 'project'
        self.project.mkdir()
        (self.project / 'agent.json').write_text(
            json.dumps({'python': f'{sys.version_info.major}.{sys.version_info.minor}'})
        )
        (self.project / 'agent.py').write_text(SOURCE)
        self.wheels = self.root / 'wheels'
        self.wheels.mkdir()

    def save(self, requirements=''):
        (self.project / 'requirements.txt').write_text(requirements)
        return self.app.import_agent(self.profile, self.project)['agent_id']

    def prepare(self, agent, **kwargs):
        job = self.app.prepare_agent_environment(self.profile, agent, **kwargs)
        self.addCleanup(job.close)
        result = job.wait(30)
        self.assertNotEqual(result['state'], 'preparing', result)
        return result

    def test_windows_layout_is_used_for_saved_environment_readiness(self):
        from functools import partial
        from sim_platform.agents.environments.installer import environment_python
        from sim_platform.persistence.agents.environments import EnvironmentFiles

        files = EnvironmentFiles(
            self.root / 'windows-layout', python_path=partial(environment_python, platform='nt')
        )
        target = files.allocate()
        python = target / 'python' / 'Scripts' / 'python.exe'
        python.parent.mkdir(parents=True)
        python.touch()
        (target / 'python' / 'pyvenv.cfg').touch()
        specification = {'runtime': 'test-native-windows'}
        published = files.publish(target, specification, [])
        self.assertEqual(files.find(specification), published)
        self.assertEqual(files.executable(published), python)
        python.unlink()
        self.assertIsNone(files.find(specification))

    def test_offline_transitive_wheels_install_without_running_package_code(self):
        wheel(self.wheels, dependency='tiny_helper>=1')
        wheel(self.wheels, 'tiny_helper')
        agent = self.save('tiny_dependency>=1,<2')
        result = self.prepare(agent, wheelhouse=self.wheels)
        self.assertEqual(result['state'], 'ready', result)
        self.assertEqual(
            {p['name'] for p in result['environment']['packages']},
            {'tiny_dependency', 'tiny_helper'},
        )
        ready = self.app.agent_environment(self.profile, agent)
        self.assertEqual(ready, result['environment'])
        base = self.root / 'app' / 'agents' / 'environments' / ready['environment_ref']
        self.assertTrue(list((base / 'python').rglob('tiny_dependency.py')))
        with patch(
            'sim_platform.agents.environments.preparation.install_environment',
            side_effect=AssertionError('must reuse'),
        ):
            again = self.prepare(agent, wheelhouse=self.wheels)
        self.assertTrue(again['reused'])
        self.assertEqual(again['environment'], ready)
        with Platform(self.root / 'app') as reopened:
            self.assertEqual(reopened.agent_environment(self.profile, agent), ready)

    def test_empty_environment_and_updated_agent_reuse(self):
        agent = self.save()
        first = self.prepare(agent)
        self.assertEqual(first['state'], 'ready', first)
        second = self.app.import_agent(
            self.profile, self.project, agent_id=agent, expected_revision=1
        )
        self.assertEqual(second['revision'], 2)
        self.assertTrue(self.prepare(agent)['reused'])

    def test_missing_dependency_fails_without_publishing_ready(self):
        agent = self.save('does_not_exist_fixture==9')
        result = self.prepare(agent, wheelhouse=self.wheels)
        self.assertEqual(result['state'], 'failed')
        self.assertEqual(result['error']['code'], 'DEPENDENCY_UNAVAILABLE')
        self.assertIsNone(self.app.agent_environment(self.profile, agent))
        self.assertEqual(
            list((self.root / 'app' / 'agents' / 'environments').glob('*/environment.json')), []
        )

    def test_concurrent_preparations_publish_one_reusable_environment(self):
        agent = self.save()
        first = self.app.prepare_agent_environment(self.profile, agent)
        second = self.app.prepare_agent_environment(self.profile, agent)
        self.addCleanup(first.close)
        self.addCleanup(second.close)
        a, b = first.wait(30), second.wait(30)
        self.assertEqual(a['state'], 'ready', a)
        self.assertEqual(b['state'], 'ready', b)
        self.assertEqual(a['environment'], b['environment'])
        self.assertEqual(sum([a['reused'], b['reused']]), 1)

    def test_failed_new_requirements_leave_earlier_revision_environment_ready(self):
        agent = self.save()
        original = self.prepare(agent)['environment']
        (self.project / 'requirements.txt').write_text('does_not_exist_fixture==9')
        self.app.import_agent(self.profile, self.project, agent_id=agent, expected_revision=1)
        result = self.prepare(agent, wheelhouse=self.wheels)
        self.assertEqual(result['state'], 'failed')
        self.assertEqual(self.app.agent_environment(self.profile, agent, 1), original)
        self.assertIsNone(self.app.agent_environment(self.profile, agent, 2))

    def test_source_downloads_need_explicit_choice(self):
        result = self.prepare(self.save('tiny_dependency'))
        self.assertEqual(result['error']['code'], 'DEPENDENCY_SOURCE_REQUIRED')

    def test_unsafe_startup_hook_and_direct_url_are_rejected_before_install(self):
        for hook, dependency in (
            (True, None),
            (False, 'escape @ https://invalid.example/escape.whl'),
        ):
            path = wheel(self.wheels, hook=hook, dependency=dependency)
            result = self.prepare(self.save('tiny_dependency'), wheelhouse=self.wheels)
            self.assertEqual(result['error']['code'], 'UNSAFE_DEPENDENCY', result)
            path.unlink()

    def test_incompatible_python_fails_clearly(self):
        (self.project / 'agent.json').write_text(json.dumps({'python': '0.0'}))
        result = self.prepare(self.save())
        self.assertEqual(result['error']['code'], 'INVALID_PYTHON_VERSION')

    def test_cancel_job_and_platform_close_cleanup(self):
        agent = self.save()

        def wait_for_cancel(target, specification, cancelled, publish, **kwargs):
            publish('installing')
            while not cancelled():
                time.sleep(0.01)
            raise PreparationCancelled()

        with patch(
            'sim_platform.agents.environments.preparation.install_environment',
            side_effect=wait_for_cancel,
        ):
            job = self.app.prepare_agent_environment(self.profile, agent)
            until = time.monotonic() + 3
            while job.snapshot()['phase'] != 'installing' and time.monotonic() < until:
                time.sleep(0.01)
            self.assertEqual(job.snapshot()['phase'], 'installing')
            self.app.close()
            self.assertEqual(job.snapshot()['state'], 'cancelled')
        self.assertEqual(
            list((self.root / 'app' / 'agents' / 'environments').glob('*/environment.json')), []
        )

    def test_cancel_reaps_a_real_transient_subprocess(self):
        started = time.monotonic()
        with self.assertRaises(PreparationCancelled):
            run_command(
                [sys.executable, '-I', '-c', 'import time; time.sleep(30)'],
                lambda: time.monotonic() - started > 0.15,
            )
        self.assertLess(time.monotonic() - started, 2)


class PreparationCommandTests(unittest.TestCase):
    def test_windows_commands_use_job_ownership_and_release_it(self):
        from types import SimpleNamespace

        process, family = Mock(), Mock()
        with (
            patch.object(commands, 'os', SimpleNamespace(name='nt')),
            patch.object(commands.subprocess, 'Popen', return_value=process) as launch,
            patch(
                'sim_platform.agents.hosting.processes.windows.WindowsProcessFamily',
                return_value=family,
            ),
        ):
            child, owner = commands._launch_command(['python', '-V'], None, None, {})
            self.assertIs(child, process)
            self.assertIs(owner, family)
            self.assertFalse(launch.call_args.kwargs['start_new_session'])
            family.attach.assert_called_once_with(process)
            commands._stop_command(child, owner)
        family.close.assert_called_once_with()
        process.wait.assert_called_once_with(timeout=1)

    def test_failed_windows_attachment_reaps_child_and_job(self):
        from types import SimpleNamespace

        process, family = Mock(), Mock()
        process.poll.return_value = None
        family.attach.side_effect = OSError('job attachment failed')
        with (
            patch.object(commands, 'os', SimpleNamespace(name='nt')),
            patch.object(commands.subprocess, 'Popen', return_value=process),
            patch(
                'sim_platform.agents.hosting.processes.windows.WindowsProcessFamily',
                return_value=family,
            ),
        ):
            with self.assertRaisesRegex(OSError, 'job attachment failed'):
                commands._launch_command(['python', '-V'], None, None, {})
        family.close.assert_called_once_with()
        process.kill.assert_called_once_with()
        process.wait.assert_called_once_with()
