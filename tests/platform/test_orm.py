"""ORM schema parity, original-v1 migration and transaction-boundary acceptance."""

from contextlib import closing
from dataclasses import asdict
import json
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from sim_platform import (
    ContractError,
    Platform,
    Profile,
    SimulationAccount,
    DecisionMaker,
    Run,
    LedgerEvent,
)
from sim_platform.examples.fixture_executor import FixtureExecutor
from sim_platform.persistence.sql.models import AccountRecord, ProfileRecord, RunRecord
from sim_platform.persistence.sql.repositories import Repositories
from sim_platform.persistence.sql.migrations import (
    v0002_orm,
    v0003_execution,
    SCHEMA_VERSION,
    REVISION,
)

FIXTURES = ROOT / "tests" / "platform" / "fixtures"


class ORMTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory(prefix="account-orm-")
        self.directory = Path(self.temporary.name)
        self.golden = json.loads((FIXTURES / "platform_v1_expected.json").read_text())

    def tearDown(self):
        self.temporary.cleanup()

    def legacy(self, name="controlled"):
        directory = self.directory / name
        directory.mkdir()
        with closing(sqlite3.connect(directory / "platform.sqlite")) as db, db:
            db.executescript((FIXTURES / "platform_v1.sql").read_text())
        return directory

    def rows(self, directory):
        with closing(sqlite3.connect(directory / "platform.sqlite")) as db, db:
            return {
                table: db.execute(f"SELECT * FROM {table} ORDER BY 1,2").fetchall()
                for table in ("profiles", "accounts", "makers", "runs", "events")
            }

    def test_v1_upgrade_preserves_all_rows_and_recorded_results(self):
        directory = self.legacy()
        before = self.rows(directory)
        with Platform(directory) as app:
            snapshot = asdict(app.snapshot(self.golden["profile_id"], self.golden["account_id"]))
            self.assertEqual(json.loads(json.dumps(snapshot)), self.golden["expected_snapshot"])
            self.assertEqual(
                app.events(self.golden["profile_id"], self.golden["account_id"]),
                self.golden["expected_events"],
            )
            old_run = app.run(self.golden["profile_id"], self.golden["run_id"])
            self.assertEqual(old_run["policy_state"], {"actions": 2})
            self.assertEqual(old_run["status"], "completed")
        self.assertEqual(self.rows(directory), before)  # All IDs and original JSON TEXT bytes too.
        with closing(sqlite3.connect(directory / "platform.sqlite")) as db, db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
            self.assertEqual(
                db.execute("SELECT version_num FROM alembic_version").fetchall(), [(REVISION,)]
            )
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])

    def legacy_v2(self):
        from sim_platform.persistence.sql.database import _engine

        directory = self.legacy()
        engine = _engine(directory / 'platform.sqlite', migration=True)
        try:
            with engine.begin() as connection:
                v0002_orm.upgrade(connection)
                connection.exec_driver_sql('PRAGMA user_version = 2')
        finally:
            engine.dispose()
        return directory

    def test_v2_execution_upgrade_preserves_rows_and_constraints(self):
        directory = self.legacy_v2()
        before = self.rows(directory)
        with Platform(directory) as app:
            self.assertEqual(
                app.snapshot(self.golden['profile_id'], self.golden['account_id']).cash, 94_380_000
            )
            checks = inspect(app._database.engine).get_check_constraints('events')
            self.assertIn(
                'cancelled', next(c['sqltext'] for c in checks if c['name'] == 'event_kind')
            )
        self.assertEqual(self.rows(directory), before)
        with closing(sqlite3.connect(directory / 'platform.sqlite')) as db, db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], SCHEMA_VERSION)
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_v3_upgrade_failure_restores_v2_database(self):
        directory = self.legacy_v2()
        original = (directory / 'platform.sqlite').read_bytes()
        upgrade = v0003_execution.upgrade

        def failed(connection):
            upgrade(connection)
            raise RuntimeError('injected v3 migration failure')

        with patch.object(v0003_execution, 'upgrade', failed), self.assertRaises(RuntimeError):
            Platform(directory)
        self.assertEqual((directory / 'platform.sqlite').read_bytes(), original)
        with Platform(directory) as app:
            self.assertEqual(
                app.snapshot(self.golden['profile_id'], self.golden['account_id']).cash, 94_380_000
            )

    def test_migrated_active_run_can_continue_and_retry(self):
        directory = self.legacy()
        with Platform(directory) as app:
            run_id = self.golden["active_run_id"]
            session = app.decision_session(
                self.golden["profile_id"], run_id, self.golden["maker_id"]
            )
            fixture = FixtureExecutor(app, self.golden["profile_id"], run_id)
            fixture.accept(f"{run_id}:intent:pending", "continued", 101)
            fixture.fill("continued", "continued", 1, 1_000_000, 0, 102)
            before = session.observe()
            fixture.fill("continued", "continued", 1, 1_000_000, 0, 102)
            self.assertEqual(session.observe(), before)
            self.assertEqual(before.cash, 93_380_000)
            self.assertEqual(before.positions[0].quantity, 7)

    def test_fresh_and_upgraded_schema_constraints_match(self):
        legacy = self.legacy()
        fresh = self.directory / "fresh"
        with Platform(legacy) as upgraded, Platform(fresh) as new:
            old = inspect(upgraded._database.engine)
            current = inspect(new._database.engine)
            for table in current.get_table_names():
                self.assertEqual(
                    {(c["name"], c["sqltext"]) for c in old.get_check_constraints(table)},
                    {(c["name"], c["sqltext"]) for c in current.get_check_constraints(table)},
                )
                self.assertEqual(
                    {i["name"] for i in old.get_indexes(table)},
                    {i["name"] for i in current.get_indexes(table)},
                )
                self.assertEqual(
                    [(c["name"], c["nullable"]) for c in old.get_columns(table)],
                    [(c["name"], c["nullable"]) for c in current.get_columns(table)],
                )

    def test_migration_failure_restores_original_schema_and_rows(self):
        directory = self.legacy()
        original = (directory / "platform.sqlite").read_bytes()
        upgrade = v0002_orm.upgrade

        def fail_after_schema_changes(connection):
            upgrade(connection)
            raise RuntimeError("injected migration failure")

        with patch.object(v0002_orm, "upgrade", fail_after_schema_changes):
            with self.assertRaisesRegex(RuntimeError, "injected"):
                Platform(directory)
        self.assertEqual((directory / "platform.sqlite").read_bytes(), original)
        with Platform(directory) as app:
            self.assertEqual(
                app.snapshot(self.golden["profile_id"], self.golden["account_id"]).cash, 94_380_000
            )

    def test_invalid_legacy_rows_abort_upgrade(self):
        directory = self.legacy()
        with closing(sqlite3.connect(directory / "platform.sqlite")) as db, db:
            db.execute("UPDATE accounts SET currency='INVALID'")
        original = (directory / "platform.sqlite").read_bytes()
        with self.assertRaises(IntegrityError):
            Platform(directory)
        self.assertEqual((directory / "platform.sqlite").read_bytes(), original)

    def test_invalid_legacy_foreign_key_aborts_upgrade(self):
        directory = self.legacy()
        with closing(sqlite3.connect(directory / "platform.sqlite")) as db, db:
            db.execute("UPDATE accounts SET profile_id='missing'")
        original = (directory / "platform.sqlite").read_bytes()
        with self.assertRaises(ContractError):
            Platform(directory)
        self.assertEqual((directory / "platform.sqlite").read_bytes(), original)

    def test_revision_mismatch_and_claimed_schema_layout_refused(self):
        directory = self.legacy()
        with Platform(directory):
            pass
        with closing(sqlite3.connect(directory / "platform.sqlite")) as db, db:
            db.execute("UPDATE alembic_version SET version_num='future'")
        original = (directory / "platform.sqlite").read_bytes()
        with self.assertRaises(ContractError):
            Platform(directory)
        self.assertEqual((directory / "platform.sqlite").read_bytes(), original)
        missing = self.legacy("missing")
        with closing(sqlite3.connect(missing / "platform.sqlite")) as db, db:
            db.execute("DROP TABLE events")
        with self.assertRaises(ContractError):
            Platform(missing)

    def test_orm_relationships_and_typed_repository_values(self):
        directory = self.legacy()
        with Platform(directory) as app:
            with app._database.unit_of_work() as session:
                profile = session.get(ProfileRecord, self.golden["profile_id"])
                self.assertEqual(len(profile.accounts), 2)
                self.assertEqual(profile.accounts[0].profile.id, profile.id)
                run = session.get(RunRecord, self.golden["active_run_id"])
                self.assertEqual(run.account.id, self.golden["account_id"])
                self.assertEqual(run.maker.id, self.golden["maker_id"])
                repo = Repositories(session)
                self.assertIsInstance(repo.profiles.get(profile.id), Profile)
                self.assertIsInstance(repo.accounts.get(run.account_id), SimulationAccount)
                self.assertIsInstance(repo.makers.get(run.maker_id), DecisionMaker)
                self.assertIsInstance(repo.runs.get(run.id), Run)
                self.assertIsInstance(repo.events.for_account(run.account_id)[0], LedgerEvent)

    def test_database_constraints_cover_orm_bypass(self):
        directory = self.legacy()
        with Platform(directory) as app:
            for value in ("EUR", "INVALID"):
                with self.assertRaises(IntegrityError):
                    with app._database.unit_of_work(write=True) as session:
                        session.add(
                            AccountRecord(
                                id="bad",
                                profile_id=self.golden["profile_id"],
                                name="bad",
                                currency=value,
                            )
                        )
                        session.flush()
            with self.assertRaises(IntegrityError):
                with app._database.unit_of_work(write=True) as session:
                    session.add(
                        AccountRecord(id="bad", profile_id="missing", name="bad", currency="USD")
                    )
                    session.flush()
            with self.assertRaises(IntegrityError):
                with app._database.unit_of_work(write=True) as session:
                    existing = session.get(RunRecord, self.golden["active_run_id"])
                    session.add(
                        RunRecord(
                            id="second",
                            account_id=existing.account_id,
                            maker_id=existing.maker_id,
                            status="running",
                            time_ns=0,
                            universe=["AAPL"],
                            config={},
                            initial_snapshot={},
                            policy_state={},
                        )
                    )
                    session.flush()

    def test_repeated_startup_does_not_remigrate(self):
        directory = self.legacy()
        with Platform(directory):
            pass
        before = self.rows(directory)
        with patch.object(
            v0002_orm, "upgrade", side_effect=AssertionError("must not upgrade again")
        ):
            for _ in range(2):
                with Platform(directory):
                    pass
        self.assertEqual(self.rows(directory), before)

    def test_domain_models_reject_invalid_identity_and_status(self):
        for constructor in (
            lambda: Profile("id", ""),
            lambda: SimulationAccount("id", "owner", "A", "EUR"),
            lambda: DecisionMaker("id", "owner", "M", "unknown", "1", {}),
            lambda: Run("id", "account", "maker", "unknown", 0, ["AAPL"], {}, {}, {}),
            lambda: LedgerEvent("account", 1, "id", None, None, "fill", {}),
        ):
            with self.assertRaises(ContractError):
                constructor()

    def test_closed_platform_does_not_reopen_connections(self):
        app = Platform(self.directory)
        app.close()
        with self.assertRaises(ContractError):
            app.profiles()


if __name__ == "__main__":
    unittest.main()
