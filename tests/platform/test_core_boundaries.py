"""Incremental persistence, layer boundaries and failure-safe lifecycle."""

from contextlib import closing
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
from sim_platform import Platform, ContractError, Intent
from sim_platform.research import ResearchSession
from sim_platform.research.batch import compare
from sim_platform.decisions import (
    HumanPolicy,
    BuyHoldSell,
    DecisionConfig,
    make_policy,
    policy_descriptor,
)
from sim_platform.persistence.sql.repositories import (
    EventRepository,
    RunRepository,
    CheckpointRepository,
)
from sim_platform.persistence.sql.migrations import v0004_incremental
from sim_platform.examples.market_demo import write_fixture

LIBRARY = None
if __name__ == '__main__' and len(sys.argv) > 1 and not sys.argv[1].startswith('-'):
    LIBRARY = sys.argv.pop(1)


class CoreBoundaryTests(unittest.TestCase):
    def research_session(self, directory, library=None):
        platform = Platform(directory)
        self.addCleanup(platform.close)
        return ResearchSession(platform, library)

    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name)
        self.source, self.news = write_fixture(self.path / 'fixture')
        self.controller = self.research_session(self.path / 'accounts', LIBRARY)
        self.addCleanup(self.controller.close)
        self.app = self.controller.app
        self.profile = self.app.create_profile('Researcher')
        self.account = self.app.create_account(self.profile, 'Account', 100_000_000)
        self.controller.load(self.source, news=self.news)

    def start(self, policy='human'):
        self.controller.start(
            self.profile, self.account, policy=policy, execution_mode='controlled'
        )
        return self.controller.runner

    def test_batch_tick_does_not_replay_ledger_or_load_decision_history_or_render(self):
        runner = self.start('rule')
        with (
            patch.object(
                EventRepository, 'for_account', side_effect=AssertionError('full ledger read')
            ),
            patch.object(RunRepository, 'records', side_effect=AssertionError('full audit read')),
            patch.object(
                self.controller, 'view', side_effect=AssertionError('unnecessary rendering')
            ),
        ):
            runner.play()
            while runner.snapshot.status == 'running':
                self.controller.tick(render=False)
        events = self.app.events(self.profile, self.account)
        self.assertEqual(
            self.app.snapshot(self.profile, self.account),
            self.app.replay_snapshot(self.profile, self.account, events),
        )
        self.assertTrue(runner.records)

    def test_checkpoint_and_event_rollback_together_and_other_connection_sees_commits(self):
        runner = self.start()
        for _ in range(3):
            runner.step()
        before = runner.snapshot.account
        with patch.object(
            CheckpointRepository, 'save', side_effect=OSError('checkpoint disk failure')
        ):
            with self.assertRaises(OSError):
                runner.act(
                    [
                        {
                            'kind': 'submit',
                            'intent': asdict(Intent('buy', 'AAPL', 'buy', 1, 1_901_000)),
                        }
                    ]
                )
        after = runner.snapshot.account
        self.assertEqual(after.cash, before.cash)
        self.assertEqual(after.orders, before.orders)
        self.assertEqual(runner.records, [])
        with Platform(self.path / 'accounts') as other:
            self.assertEqual(other.snapshot(self.profile, self.account), after)
            self.assertEqual(
                other.snapshot(self.profile, self.account),
                other.replay_snapshot(
                    self.profile, self.account, other.events(self.profile, self.account)
                ),
            )

    def test_decision_audit_is_append_only_and_public_tail_is_ordered(self):
        runner = self.start()
        for index in range(6):
            runner.act([], {'iteration': index})
        with closing(sqlite3.connect(self.path / 'accounts' / 'platform.sqlite')) as db, db:
            state = json.loads(
                db.execute(
                    'SELECT policy_state FROM runs WHERE id=?', (self.controller.run_id,)
                ).fetchone()[0]
            )
            self.assertNotIn('records', state)
            self.assertEqual(state['_record_count'], 6)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM decision_records').fetchone()[0], 6)
        self.assertEqual(
            self.app.decision_records(self.profile, self.controller.run_id, limit=2),
            runner.records[-2:],
        )
        self.assertEqual(
            self.app.run(self.profile, self.controller.run_id)['policy_state']['records'],
            runner.records,
        )

    def test_v3_journal_upgrade_and_failed_upgrade_preserve_existing_results(self):
        runner = self.start()
        runner.act([], {'memory': 42})
        self.controller.stop()
        expected = self.controller.report()
        run_id = self.controller.run_id
        self.controller.close()
        database = self.path / 'accounts' / 'platform.sqlite'
        with closing(sqlite3.connect(database)) as db, db:
            db.execute(
                'UPDATE runs SET policy_state=? WHERE id=?',
                (json.dumps(expected['run']['policy_state']), run_id),
            )
            db.execute('DROP TABLE agent_experiments')
            db.execute('DROP TABLE agent_revisions')
            db.execute('DROP TABLE research_archives')
            db.execute('DROP TABLE run_checkpoints')
            db.execute('DROP TABLE ledger_items')
            db.execute('DROP TABLE decision_records')
            db.execute('DROP TABLE account_checkpoints')
            db.execute("UPDATE alembic_version SET version_num='0003_execution'")
            db.execute('PRAGMA user_version=3')
        original = database.read_bytes()
        upgrade = v0004_incremental.upgrade

        def fail(connection):
            upgrade(connection)
            raise OSError('migration failure')

        with patch.object(v0004_incremental, 'upgrade', fail), self.assertRaises(OSError):
            Platform(self.path / 'accounts')
        self.assertEqual(database.read_bytes(), original)
        with Platform(self.path / 'accounts') as app:
            from sim_platform.research import run_report

            self.assertEqual(run_report(app, self.profile, run_id), expected)

    def test_failed_run_creation_does_not_leave_a_maker(self):
        self.start()
        makers = self.app.makers(self.profile)
        policy = BuyHoldSell()
        from sim_platform.execution import ExecutionConfig

        with self.assertRaises(ContractError):
            self.app.create_managed_decision_run(
                self.profile, self.account, 'AAPL', policy, DecisionConfig(), ExecutionConfig()
            )
        self.assertEqual(self.app.makers(self.profile), makers)

    def test_checkpoint_can_be_rebuilt_and_wrong_account_checkpoint_is_refused(self):
        self.start('rule')
        self.controller.runner.play()
        while self.controller.runner.snapshot.status == 'running':
            self.controller.tick(render=False)
        expected = self.app.snapshot(self.profile, self.account)
        database = self.path / 'accounts' / 'platform.sqlite'
        with closing(sqlite3.connect(database)) as db, db:
            row = db.execute(
                'SELECT state FROM account_checkpoints WHERE account_id=?', (self.account,)
            ).fetchone()
            state = json.loads(row[0])
            state['account_id'] = 'another-account'
            db.execute(
                'UPDATE account_checkpoints SET state=? WHERE account_id=?',
                (json.dumps(state), self.account),
            )
        with self.assertRaises(ContractError):
            self.app.snapshot(self.profile, self.account)
        self.assertEqual(self.app.rebuild_account(self.profile, self.account), expected)
        self.assertEqual(self.app.snapshot(self.profile, self.account), expected)

    def test_invalid_run_configuration_is_rejected_before_persisting_run(self):
        maker = self.app.create_maker(self.profile, 'Human', 'human', '1')
        for config in ({'execution': {'latency_ns': -1}}, {'decision': {}}, {'execution': None}):
            with self.assertRaises(ContractError):
                self.app.create_run(self.profile, self.account, maker, ['AAPL'], config)
        self.assertEqual(self.app.runs(self.profile), [])

    def test_close_releases_database_even_when_market_close_raises(self):
        with patch.object(
            self.controller.market, 'close', side_effect=OSError('native close failure')
        ):
            with self.assertRaisesRegex(OSError, 'native close'):
                self.app.close()
        self.assertTrue(self.app._database._closed)
        self.controller.close()  # Idempotent, including after partial cleanup failure.
        self.controller.market.close()  # Release the actual test fixture handle.

    def test_execution_stop_unbinds_without_closing_borrowed_market(self):
        runner = self.start()
        with patch.object(
            self.controller.market, 'close', side_effect=AssertionError('borrowed market closed')
        ):
            runner.close()
        self.assertFalse(self.controller.market.has_execution_owner)
        self.assertFalse(self.app._resources.has_execution(self.controller.run_id))
        self.assertEqual(runner.snapshot.status, 'stopped')

    def test_platform_close_stops_its_active_execution_before_closing_database(self):
        self.start()
        self.app.close()
        with Platform(self.path / 'accounts') as reopened:
            self.assertEqual(
                reopened.run(self.profile, self.controller.run_id)['status'], 'stopped'
            )
        self.assertFalse(self.controller.market.has_execution_owner)

    def test_policy_contract_and_shared_factory_reject_invalid_adapters(self):
        for name, cls in [('human', HumanPolicy), ('rule', BuyHoldSell)]:
            self.assertIsInstance(make_policy(name), cls)
        for policy in [
            object(),
            type('MissingDecision', (), dict(kind='rule', name='rule', version='1', requires=()))(),
        ]:
            with self.assertRaises(ContractError):
                policy_descriptor(policy)
        with patch(
            'sim_platform.research.batch.ResearchBatch.evaluate',
            side_effect=AssertionError('evaluation started'),
        ):
            with self.assertRaises(ContractError):
                compare(self.app, self.source, [{'policy': 'rule'}, {'quantity': 0}])

    def test_domain_and_market_imports_do_not_load_orm_or_frontends(self):
        program = "import sys; import sim_platform.domain.ledger; import sim_platform.market; assert not any(n.startswith(('sqlalchemy', 'alembic', 'PySide6', 'sim_platform.application', 'sim_platform.cli', 'sim_platform.research')) for n in sys.modules)"
        subprocess.run([sys.executable, '-c', program], cwd=ROOT, check=True)

    def test_failed_evaluation_returns_nonzero_but_still_emits_report(self):
        from argparse import Namespace
        from contextlib import redirect_stdout
        from io import StringIO
        from sim_platform.cli.commands.research import _report

        output = StringIO()
        with redirect_stdout(output):
            status = _report(Namespace(output=None), {'summary': {'status': 'failed'}})
        self.assertEqual(status, 1)
        self.assertEqual(json.loads(output.getvalue())['summary']['status'], 'failed')


if __name__ == '__main__':
    unittest.main()
