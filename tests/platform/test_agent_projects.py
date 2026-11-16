"""Immutable project import, static checking and cross-profile source ownership."""

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest
from sim_platform import Platform, ContractError
from sim_platform.agents.preparation.errors import PreparationError

SOURCE = '''raise RuntimeError("must not import during preparation")

def create_agent(context):
    return MyAgent(context)
'''


class AgentProjectTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.app = Platform(self.root / 'app')
        self.addCleanup(self.app.close)
        self.profile = self.app.create_profile('Owner')
        self.source = self.root / 'strategy.py'
        self.source.write_bytes((SOURCE).encode())

    def test_single_file_snapshot_survives_original_edit_and_reopen(self):
        saved = self.app.import_agent(self.profile, self.source)
        self.source.write_bytes(('broken source').encode())
        self.assertEqual(
            self.app.agent_project_file(self.profile, saved['agent_id'], 'agent.py'), SOURCE
        )
        self.assertEqual(saved['definition']['package'].keys(), {'project_ref'})
        with Platform(self.root / 'app') as reopened:
            self.assertEqual(
                reopened.agent_project(self.profile, saved['agent_id'])['entrypoint'],
                'agent:create_agent',
            )
        self.assertEqual(len(self.app.agents(self.profile)), 1)
        self.assertEqual(self.app.runs(self.profile), [])

    def test_folder_helpers_dependencies_and_private_file_exclusion(self):
        folder = self.root / 'project'
        (folder / 'tools').mkdir(parents=True)
        (folder / 'agent.py').write_bytes((SOURCE).encode())
        (folder / 'tools' / 'math.py').write_bytes(b'def double(value): return value * 2\n')
        (folder / 'requirements.txt').write_text('example>=1,<2\n# comment\nexample>=1,<2\n')
        (folder / '.env').write_text('API_KEY=do-not-copy')
        (folder / 'secret.key').write_text('do-not-copy')
        (folder / '.venv').mkdir()
        (folder / '.venv' / 'marker').write_text('ignored')
        report = self.app.check_agent_project(folder)
        self.assertEqual(report['requirements'], ['example>=1,<2'])
        self.assertEqual(report['excluded'], ['.env', '.venv', 'secret.key'])
        saved = self.app.import_agent(self.profile, folder)
        self.assertEqual(
            self.app.agent_project_file(self.profile, saved['agent_id'], 'tools/math.py'),
            'def double(value): return value * 2\n',
        )
        for name in ('.env', '../project.json', '/etc/passwd'):
            with self.assertRaises(ContractError):
                self.app.agent_project_file(self.profile, saved['agent_id'], name)
        copied = (
            self.root
            / 'app'
            / 'agents'
            / 'projects'
            / saved['definition']['package']['project_ref']
        )
        self.assertNotIn(
            'do-not-copy', ''.join(p.read_text() for p in copied.rglob('*') if p.is_file())
        )

    def test_update_appends_revision_and_stale_edit_removes_candidate(self):
        first = self.app.import_agent(self.profile, self.source)
        self.source.write_bytes((SOURCE + '\nVALUE = 2\n').encode())
        second = self.app.import_agent(
            self.profile, self.source, agent_id=first['agent_id'], expected_revision=1
        )
        self.assertEqual(second['revision'], 2)
        self.assertNotEqual(first['definition']['package'], second['definition']['package'])
        self.assertEqual(
            self.app.agent_project_file(self.profile, first['agent_id'], 'agent.py', 1), SOURCE
        )
        snapshots = list((self.root / 'app' / 'agents' / 'projects').iterdir())
        with self.assertRaises(ContractError):
            self.app.import_agent(
                self.profile, self.source, agent_id=first['agent_id'], expected_revision=1
            )
        self.assertEqual(list((self.root / 'app' / 'agents' / 'projects').iterdir()), snapshots)

    def test_other_profile_cannot_read_or_forge_a_project_reference(self):
        saved = self.app.import_agent(self.profile, self.source)
        other = self.app.create_profile('Other')
        with self.assertRaises(ContractError):
            self.app.agent_project(other, saved['agent_id'])
        forged = dict(saved['definition'], agent_id='forged')
        self.app.create_agent(other, forged)
        with self.assertRaises(ContractError):
            self.app.agent_project(other, 'forged')

    def test_invalid_factory_syntax_and_requirements_are_static_failures(self):
        cases = [
            ('', 'MISSING_ENTRYPOINT'),
            ('def create_agent(:', 'INVALID_PYTHON'),
            ('async def create_agent(context): pass', 'INVALID_ENTRYPOINT'),
            ('def create_agent(context, extra): pass', 'INVALID_ENTRYPOINT'),
        ]
        for source, code in cases:
            self.source.write_bytes((source).encode())
            with self.assertRaises(PreparationError) as raised:
                self.app.import_agent(self.profile, self.source)
            self.assertEqual(raised.exception.code, code)
        folder = self.root / 'bad'
        folder.mkdir()
        (folder / 'agent.py').write_bytes((SOURCE).encode())
        for dependency in (
            '-r hidden.txt',
            'git+https://example.com/x',
            'pkg @ https://example.com/x.whl',
            '../local',
            'pkg; python_version>"3"',
        ):
            (folder / 'requirements.txt').write_text(dependency)
            with self.assertRaisesRegex(PreparationError, 'package/version'):
                self.app.import_agent(self.profile, folder)
        self.assertEqual(self.app.agents(self.profile), [])

    def test_symlinks_and_special_files_are_not_copied(self):
        folder = self.root / 'bad'
        folder.mkdir()
        (folder / 'agent.py').write_bytes((SOURCE).encode())
        (folder / 'escape.py').symlink_to(self.source)
        with self.assertRaisesRegex(ContractError, 'symlink'):
            self.app.import_agent(self.profile, folder)

    def test_failed_database_write_removes_only_the_candidate(self):
        with patch.object(
            self.app._operations, 'create_agent', side_effect=OSError('commit failure')
        ):
            with self.assertRaises(OSError):
                self.app.import_agent(self.profile, self.source)
        self.assertEqual(list((self.root / 'app' / 'agents' / 'projects').iterdir()), [])

    def test_resident_definition_uses_same_project_without_platform_schedule(self):
        saved = self.app.import_agent(
            self.profile, self.source, integration='python_carrier', requires=[]
        )
        self.assertEqual(saved['definition']['carrier'], 'custom')
        self.assertNotIn('schedule', saved['definition'])
        self.assertEqual(
            self.app.agent_project(self.profile, saved['agent_id'])['entrypoint'],
            'agent:create_agent',
        )

    def test_obsolete_hand_written_package_is_rejected(self):
        saved = self.app.import_agent(self.profile, self.source)
        invalid = dict(
            saved['definition'],
            agent_id='old',
            package=dict(sha256='a' * 64, entrypoint='x:main', environment_ref='manual'),
        )
        with self.assertRaises(ContractError):
            self.app.create_agent(self.profile, invalid)
