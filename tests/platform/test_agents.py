"""Agent persistence, ownership, revision conflicts and legacy migration checks."""

from contextlib import closing
import ast
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from support.agents import definition, settings
from sim_platform import Platform, ContractError
from sim_platform.agents.configuration import validate_definition, validate_run_configuration
from sim_platform.execution import ExecutionConfig
from sim_platform.persistence.sql.repositories.agents import (
    AgentRepository,
    AgentExperimentRepository,
)
from sim_platform.persistence.sql.migrations import v0007_agents


class AgentTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name)
        self.app = Platform(self.directory)
        self.addCleanup(self.app.close)
        self.profile = self.app.create_profile('Researcher')
        self.account = self.app.create_account(self.profile, 'Account', 10000000)
        self.other = self.app.create_profile('Other')

    def prepare(self, revision=1, config=None):
        return self.app.prepare_agent_experiment(
            self.profile, self.account, 'demo', revision, settings() if config is None else config
        )

    def test_revision_persists_and_input_output_mutations_cannot_change_it(self):
        data = definition()
        saved = self.app.create_agent(self.profile, data)
        data['parameters']['nested'].append(2)
        saved['definition']['initial_prompt'] = 'tampered'
        before = self.app.agent(self.profile, 'demo', 1)
        self.assertEqual(before['definition']['parameters'], {'nested': [1]})
        updated = definition(revision=2)
        updated['name'] = 'Changed'
        self.app.update_agent(self.profile, 'demo', 1, updated)
        self.assertEqual(self.app.agent(self.profile, 'demo', 1), before)
        self.assertEqual(self.app.agent(self.profile, 'demo')['revision'], 2)
        with Platform(self.directory) as reopened:
            self.assertEqual(reopened.agent(self.profile, 'demo', 1), before)

    def test_two_connections_cannot_overwrite_the_same_revision(self):
        self.app.create_agent(self.profile, definition())
        with Platform(self.directory) as other:

            def edit(app, name):
                value = definition(revision=2)
                value['name'] = name
                try:
                    app.update_agent(self.profile, 'demo', 1, value)
                    return 'saved'
                except ContractError as exc:
                    self.assertIn('REVISION_CONFLICT', str(exc))
                    return 'conflict'

            with ThreadPoolExecutor(max_workers=2) as workers:
                results = list(
                    workers.map(lambda args: edit(*args), [(self.app, 'First'), (other, 'Second')])
                )
        self.assertEqual(sorted(results), ['conflict', 'saved'])
        self.assertEqual(self.app.agent(self.profile, 'demo')['revision'], 2)

    def test_all_three_integrations_are_metadata_only(self):
        with patch('subprocess.Popen', side_effect=AssertionError('user code executed')):
            for integration in ('configured', 'python_strategy', 'python_carrier'):
                value = definition(integration)
                value['integration'] = integration
                if integration != 'configured':
                    value['package'] = dict(project_ref='fixture-project')
                if integration == 'python_carrier':
                    value['carrier'] = 'custom'
                    value.pop('schedule')
                self.app.create_agent(self.profile, value)
        self.assertEqual(len(self.app.agents(self.profile)), 3)

    def test_profile_ownership_and_bounded_latest_revision_listing(self):
        for name in ('a', 'b', 'c'):
            self.app.create_agent(self.profile, definition(name))
        self.app.update_agent(self.profile, 'a', 1, definition('a', 2))
        self.assertEqual(
            [x['agent_id'] for x in self.app.agents(self.profile, limit=2)], ['a', 'b']
        )
        self.assertEqual(self.app.agents(self.profile, after_id='b')[0]['agent_id'], 'c')
        self.assertEqual(self.app.agents(self.profile)[0]['revision'], 2)
        for call in (
            lambda: self.app.agent(self.other, 'a'),
            lambda: self.app.update_agent(self.other, 'a', 2, definition('a', 3)),
            lambda: self.app.agents(self.profile, limit=True),
            lambda: self.app.agents(self.profile, limit=101),
        ):
            with self.assertRaises(ContractError):
                call()
        self.assertEqual(self.app.agents(self.other), [])

    def test_invalid_definitions_leave_no_rows(self):
        variants = []
        for key, value in [
            ('unexpected', 'v9'),
            ('revision', '2'),
            ('requires', ['files.read']),
            ('carrier', 'custom'),
            ('parameters', {'api_key': 'do-not-print'}),
            ('parameters', {'value': float('nan')}),
            ('parameters', {1: 'wrong-key'}),
        ]:
            candidate = definition()
            candidate[key] = value
            variants.append(candidate)
        candidate = definition()
        candidate['schedule']['interval_seconds'] = '0'
        variants.append(candidate)
        candidate = definition()
        candidate['extra'] = 1
        variants.append(candidate)
        for candidate in variants:
            with self.subTest(keys=list(candidate)), self.assertRaises(ContractError):
                self.app.create_agent(self.profile, candidate)
        self.assertEqual(self.app.agents(self.profile), [])

    def test_revision_write_failure_rolls_back_even_after_flush(self):
        self.app.create_agent(self.profile, definition())
        add = AgentRepository.add

        def fail(repo, value):
            add(repo, value)
            raise OSError('injected after flush')

        with patch.object(AgentRepository, 'add', fail), self.assertRaises(OSError):
            self.app.update_agent(self.profile, 'demo', 1, definition(revision=2))
        self.assertEqual(self.app.agent(self.profile, 'demo')['revision'], 1)
        self.app.update_agent(self.profile, 'demo', 1, definition(revision=2))

    def test_prepared_experiment_is_frozen_but_not_a_running_run(self):
        self.app.create_agent(self.profile, definition())
        before = self.app.snapshot(self.profile, self.account)
        config = settings()
        prepared = self.prepare(config=config)
        saved_id = prepared['id']
        self.assertNotIn('definition_sha256', self.app.agent(self.profile, 'demo'))
        self.assertNotIn('content_sha256', prepared)
        self.assertNotIn('initial_snapshot_sha256', prepared)
        self.assertEqual(prepared['state'], 'prepared')
        self.assertEqual(prepared['readiness'], 'requires_runtime_binding')
        self.assertEqual(prepared['bindings_required']['source_manifest_ref'], 'source-1')
        self.assertEqual(prepared['initial_snapshot'], json.loads(json.dumps(asdict(before))))
        self.assertEqual(self.app.agent_experiment(self.profile, saved_id), prepared)
        config['model_parameters']['new'] = 3
        prepared['config']['definition']['parameters']['nested'].append(999)
        self.app.update_agent(self.profile, 'demo', 1, definition(revision=2))
        original = self.app.agent_experiment(self.profile, saved_id)
        self.assertEqual(original['config']['definition']['revision'], '1')
        self.assertEqual(original['config']['definition']['parameters'], {'nested': [1]})
        self.assertEqual(original['config']['model_parameters'], {})
        self.assertEqual(self.app.runs(self.profile), [])
        self.assertEqual(self.app.makers(self.profile), [])
        self.assertEqual(self.app.snapshot(self.profile, self.account), before)
        with self.assertRaises(ContractError):
            self.app.run(self.profile, saved_id)
        with Platform(self.directory) as reopened:
            self.assertEqual(reopened.agent_experiment(self.profile, saved_id), original)
        summary = self.app.agent_experiments(self.profile)[0]
        self.assertNotIn('initial_snapshot', summary)
        self.assertNotIn('config', summary)
        self.assertNotIn('content_sha256', summary)

    def test_preparation_ownership_protected_fields_and_capabilities(self):
        self.app.create_agent(self.profile, definition())
        calls = [
            lambda: self.app.prepare_agent_experiment(
                self.other, self.account, 'demo', 1, settings()
            )
        ]
        for mutate in (
            lambda c: c.update(run_id='spoof'),
            lambda c: c.update(granted_tools=[]),
            lambda c: c.update(timeout_seconds='0'),
            lambda c: c.update(end_ns=c['start_ns']),
            lambda c: c['model_parameters'].update(api_key='do-not-print'),
        ):
            config = settings()
            mutate(config)
            calls.append(lambda c=config: self.prepare(config=c))
        for call in calls:
            with self.assertRaises(ContractError):
                call()
        self.assertEqual(self.app.agent_experiments(self.profile), [])
        saved = self.prepare()
        with self.assertRaises(ContractError):
            self.app.agent_experiment(self.other, saved['id'])

    def test_preparation_storage_failure_preserves_account_and_leaves_no_snapshot(self):
        self.app.create_agent(self.profile, definition())
        before = self.app.snapshot(self.profile, self.account)
        add = AgentExperimentRepository.add

        def fail(repo, value):
            add(repo, value)
            raise OSError('snapshot write failure')

        with patch.object(AgentExperimentRepository, 'add', fail), self.assertRaises(OSError):
            self.prepare()
        self.assertEqual(self.app.agent_experiments(self.profile), [])
        self.assertEqual(self.app.snapshot(self.profile, self.account), before)

    def test_mutated_execution_configuration_is_revalidated_before_storage(self):
        self.app.create_agent(self.profile, definition())
        execution = ExecutionConfig()
        object.__setattr__(execution, 'latency_ns', -1)
        with self.assertRaises(ContractError):
            self.app.prepare_agent_experiment(
                self.profile, self.account, 'demo', 1, settings(), execution
            )
        self.assertEqual(self.app.agent_experiments(self.profile), [])

    def test_account_identity_validation_refuses_wrong_snapshot(self):
        self.app.create_agent(self.profile, definition())
        prepared = self.prepare()
        with closing(sqlite3.connect(self.directory / 'platform.sqlite')) as db, db:
            db.execute('DROP TRIGGER agent_experiments_no_update')
            db.execute(
                "UPDATE agent_experiments SET initial_snapshot='{}' WHERE id=?", (prepared['id'],)
            )
        with self.assertRaisesRegex(ContractError, 'account identity mismatch'):
            self.app.agent_experiment(self.profile, prepared['id'])

    def test_profile_owned_account_and_experiment_pages(self):
        self.app.create_agent(self.profile, definition())
        other_account = self.app.create_account(self.other, 'Foreign account', 1000)
        with self.assertRaises(ContractError):
            self.app.prepare_agent_experiment(self.profile, other_account, 'demo', 1, settings())
        ids = sorted(self.prepare()['id'] for _ in range(3))
        self.assertEqual(
            [x['id'] for x in self.app.agent_experiments(self.profile, limit=2)], ids[:2]
        )
        self.assertEqual(
            [x['id'] for x in self.app.agent_experiments(self.profile, after_id=ids[1])], ids[2:]
        )
        self.assertEqual(self.app.agent_experiments(self.other), [])

    def test_active_account_is_not_captured_and_legacy_run_is_unchanged(self):
        self.app.create_agent(self.profile, definition())
        maker = self.app.create_maker(self.profile, 'Human', 'human', '1')
        run_id = self.app.create_run(self.profile, self.account, maker, ['AAPL'])
        before = self.app.run(self.profile, run_id)
        with self.assertRaises(ContractError):
            self.prepare()
        self.assertEqual(self.app.run(self.profile, run_id), before)

    def test_database_enforces_append_only_definitions_and_experiments(self):
        self.app.create_agent(self.profile, definition())
        self.prepare()
        with closing(sqlite3.connect(self.directory / 'platform.sqlite')) as db, db:
            for statement in (
                "UPDATE agent_revisions SET definition='{}'",
                'DELETE FROM agent_revisions',
                "UPDATE agent_experiments SET config='{}'",
                'DELETE FROM agent_experiments',
            ):
                with self.assertRaisesRegex(sqlite3.IntegrityError, 'append-only'):
                    db.execute(statement)

    def test_configuration_examples_match_production_validation(self):
        examples = json.loads(
            (ROOT / 'tests/fixtures/agent-contracts/wire-examples.json').read_text()
        )['examples']
        for example in examples:
            value = example['value']
            validator = (
                validate_definition
                if 'integration' in value
                else validate_run_configuration if 'definition' in value else None
            )
            if validator is None:
                continue
            with self.subTest(example=example['id']):
                if example['valid'] and not example.get('semantic_error'):
                    validator(value)
                else:
                    with self.assertRaises(ContractError):
                        validator(value)

    def test_no_user_invocation_or_orm_in_new_contract_service(self):
        for name in (
            'domain/agents.py',
            'agents/configuration.py',
            'application/use_cases/agents.py',
        ):
            tree = ast.parse((ROOT / 'sim_platform' / name).read_text())
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    modules = (
                        [alias.name for alias in node.names]
                        if isinstance(node, ast.Import)
                        else [node.module or '']
                    )
                    self.assertFalse(
                        any(
                            m.startswith(
                                ('sqlalchemy', 'PySide6', 'subprocess', 'sim_platform.persistence')
                            )
                            for m in modules
                        ),
                        name,
                    )
        subprocess.run(
            [
                sys.executable,
                '-c',
                "import sys; import sim_platform.domain.agents; import sim_platform.agents.configuration; assert 'sqlalchemy' not in sys.modules; assert 'PySide6' not in sys.modules",
            ],
            cwd=ROOT,
            check=True,
        )


class AgentMigrationTests(unittest.TestCase):
    def test_v6_upgrade_is_additive_and_failed_ddl_rolls_back_bytes(self):
        with TemporaryDirectory() as temp:
            directory = Path(temp)
            with Platform(directory) as app:
                profile = app.create_profile('Legacy')
                account = app.create_account(profile, 'Cash', 1000000)
                maker = app.create_maker(profile, 'Human', 'human', '1')
                run = app.create_run(profile, account, maker, ['AAPL'])
                app.finish_run(profile, run)
                before_run = app.run(profile, run)
                before_account = app.snapshot(profile, account)
            path = directory / 'platform.sqlite'
            with closing(sqlite3.connect(path)) as db, db:
                db.execute('DROP TABLE agent_experiments')
                db.execute('DROP TABLE agent_revisions')
                db.execute("UPDATE alembic_version SET version_num='0006_runtime'")
                db.execute('PRAGMA user_version=6')
                old_rows = {
                    table: db.execute('SELECT * FROM ' + table).fetchall()
                    for table in ('profiles', 'accounts', 'makers', 'runs', 'events')
                }
            original = path.read_bytes()
            upgrade = v0007_agents.upgrade

            def fail(connection):
                upgrade(connection)
                raise OSError('injected v7 DDL failure')

            with patch.object(v0007_agents, 'upgrade', fail), self.assertRaises(OSError):
                Platform(directory)
            self.assertEqual(path.read_bytes(), original)
            with Platform(directory) as app:
                self.assertEqual(app.run(profile, run), before_run)
                self.assertEqual(app.snapshot(profile, account), before_account)
                self.assertEqual(app.agents(profile), [])
            with closing(sqlite3.connect(path)) as db, db:
                self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 7)
                self.assertNotIn(
                    'definition_sha256',
                    [row[1] for row in db.execute('PRAGMA table_info(agent_revisions)')],
                )
                self.assertNotIn(
                    'content_sha256',
                    [row[1] for row in db.execute('PRAGMA table_info(agent_experiments)')],
                )
                for table, rows in old_rows.items():
                    self.assertEqual(db.execute('SELECT * FROM ' + table).fetchall(), rows)

    def test_missing_append_only_guard_refuses_open_without_repair(self):
        with TemporaryDirectory() as temp:
            directory = Path(temp)
            with Platform(directory):
                pass
            path = directory / 'platform.sqlite'
            with closing(sqlite3.connect(path)) as db, db:
                db.execute('DROP TRIGGER agent_revisions_no_update')
            original = path.read_bytes()
            with self.assertRaisesRegex(ContractError, 'append-only trigger'):
                Platform(directory)
            self.assertEqual(path.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
