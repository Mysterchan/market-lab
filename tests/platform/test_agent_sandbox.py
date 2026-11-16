"""Optional OS sandbox around an imported Agent: writes and network are confined."""

import json
import os
import sys
import unittest

from sim_platform import ContractError, Platform
from sim_platform.agents.hosting.processes import sandbox
from support.agents import settings
from support.clock import until
from support import prepared_market as prepared

SOURCE = '''import json, socket
from pathlib import Path

class Agent:
    def __init__(self, context):
        self.context = context

    def run(self, task):
        outcome = {}
        target = Path(self.context.settings['parameters']['outside'])
        try:
            target.write_text('escaped')
            outcome['outside_write'] = 'allowed'
        except OSError as exc:
            outcome['outside_write'] = type(exc).__name__
        try:
            Path('inside.txt').write_text('fine')
            outcome['inside_write'] = 'allowed'
        except OSError as exc:
            outcome['inside_write'] = type(exc).__name__
        try:
            socket.create_connection(('1.1.1.1', 80), timeout=2).close()
            outcome['network'] = 'allowed'
        except OSError as exc:
            outcome['network'] = type(exc).__name__
        quote = self.context.tool('market.quote').run('AAPL')
        outcome['quote'] = quote['status']
        self.context.tool('journal.write').run(json.dumps(outcome))
        self.context.tool('control.finish').run()

def create_agent(context):
    return Agent(context)
'''


class SettingTests(unittest.TestCase):
    def test_settings_are_checked(self):
        self.assertIsNone(sandbox.validate(None))
        self.assertIsNone(sandbox.validate(dict(kind='none')))
        self.assertEqual(sandbox.validate({}), dict(kind='auto', network='none'))
        for bad in (dict(kind='docker'), dict(network='all'), dict(extra=1), 'macos'):
            with self.assertRaises(ContractError):
                sandbox.validate(bad)

    def test_commands_keep_the_platform_pipes(self):
        command = ['python', '-I', '-c', 'pass']
        podman = sandbox.wrap(
            command, dict(kind='podman', network='none'), workspace='/w', descriptors=[9, 5, 9]
        )
        self.assertIn('--preserve-fd', podman)
        self.assertEqual(podman[podman.index('--preserve-fd') + 1], '5,9')
        self.assertEqual(podman[podman.index('--network') + 1], 'none')
        bwrap = sandbox.wrap(
            command, dict(kind='bubblewrap', network='model'), workspace='/w', descriptors=[]
        )
        self.assertIn('--share-net', bwrap)
        self.assertEqual(bwrap[-4:], command)
        profile = sandbox.wrap(
            command, dict(kind='macos', network='none'), workspace='/w', descriptors=[]
        )[2]
        self.assertIn('(deny network-outbound)', profile)


def kinds():
    """Sandboxes to test: MARKET_LAB_SANDBOX_KINDS (comma separated), else the installed
    macos and bubblewrap sandboxes; Podman is tested only when named."""
    chosen = os.environ.get('MARKET_LAB_SANDBOX_KINDS')
    if chosen:
        return [k for k in chosen.split(',') if k]
    return [k for k in sandbox.available() if k != 'podman']


@unittest.skipUnless(kinds(), 'needs sandbox-exec, bubblewrap or podman')
class SandboxedAgentTests(unittest.TestCase):
    def setUp(self):
        prepared.PreparedMarketFixture.setUp(self)
        self.app = Platform(self.root / 'app')
        self.addCleanup(self.app.close)
        self.profile = self.app.create_profile('Sandbox')
        self.account = self.app.create_account(self.profile, 'Cash', 10_000_000)
        project = self.root / 'project'
        project.mkdir()
        python = os.environ.get(
            'AGENT_TEST_PYTHON', f'{sys.version_info.major}.{sys.version_info.minor}'
        )
        (project / 'agent.json').write_text(json.dumps(dict(python=python)))
        (project / 'agent.py').write_text(SOURCE)
        self.outside = self.root / 'outside.txt'
        self.saved = self.app.import_agent(
            self.profile,
            project,
            integration='python_strategy',
            parameters=dict(outside=str(self.outside)),
            requires=['market.quote', 'journal.write', 'control.finish'],
        )
        job = self.app.prepare_agent_environment(
            self.profile,
            self.saved['agent_id'],
            python_archive=(
                os.environ.get('AGENT_TEST_RUNTIME_ARCHIVE')
                if not self.app.python_runtimes()
                else None
            ),
        )
        try:
            self.assertEqual(job.wait(60)['state'], 'ready')
        finally:
            job.close()

    def test_writes_outside_the_work_folder_and_network_are_denied(self):
        for kind in kinds():
            with self.subTest(kind=kind):
                self.check(kind)

    def check(self, kind):
        account = self.app.create_account(self.profile, kind, 10_000_000)
        experiment = self.app.open_experiment(
            self.profile,
            account,
            source=self.source,
            method='agent',
            agent_id=self.saved['agent_id'],
            revision=self.saved['revision'],
            execution_mode='live',
            settings=dict(
                settings(),
                start_ns='3',
                end_ns='50000000000',
                granted_tools=self.saved['definition']['requires'],
                sandbox=dict(kind=kind, network='none'),
            ),
        )
        self.addCleanup(experiment.close)
        experiment.play()
        until(lambda: not experiment.runner.active and experiment.runner.archive_ready, 60)
        run_id = experiment.run_id
        experiment.close()
        notes = self.app.agent_journal(self.profile, run_id)
        outcome = json.loads(notes[0]['text'])
        self.assertEqual(outcome['inside_write'], 'allowed')
        self.assertNotEqual(outcome['outside_write'], 'allowed')
        self.assertNotEqual(outcome['network'], 'allowed')
        self.assertEqual(outcome['quote'], 'ok')  # the platform's own pipes still work
        self.assertFalse(self.outside.exists())
        config = self.app.run(self.profile, run_id, include_records=False)['config']
        self.assertEqual(config['agent']['sandbox'], dict(kind=kind, network='none'))


if __name__ == '__main__':
    unittest.main()
