"""Account contract verification: independent amounts, real persistence and failure cases."""

from contextlib import closing
import copy
from dataclasses import FrozenInstanceError
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from sqlalchemy.exc import IntegrityError

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from sim_platform import Platform, ContractError, Intent, MONEY_SCALE
from sim_platform.examples.demo import run_demo
from sim_platform.examples.fixture_executor import FixtureExecutor
from sim_platform.persistence.paths import data_directory

U = MONEY_SCALE


class PlatformTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory(prefix="account-tests-")
        self.directory = Path(self.temporary.name)
        self.app = Platform(self.directory)
        self.profile = self.app.create_profile("Owner")
        self.account = self.app.create_account(self.profile, "A", 10_000 * U)
        self.maker = self.app.create_maker(self.profile, "Rule", "rule", "v1", {"threshold": 2})
        self.run = self.app.create_run(self.profile, self.account, self.maker, ["AAPL"])
        self.session = self.app.decision_session(self.profile, self.run, self.maker)
        self.executor = FixtureExecutor(self.app, self.profile, self.run)

    def tearDown(self):
        self.app.close()
        self.temporary.cleanup()

    def buy(self, quantity=10, price=100 * U, fee=U):
        intent = self.session.submit(Intent("buy", "AAPL", "buy", quantity, price), 10)
        self.executor.accept(intent, "buy", 11)
        self.executor.fill("buy", "buy", quantity, price, fee, 12)

    def test_fixed_demo_and_reopen(self):
        report = run_demo(self.directory)
        self.assertEqual(report["result"], "passed")
        self.assertEqual(report["final"]["cash"], 94_380_000)
        self.assertEqual(report["final"]["positions"][0]["cost"], 6_006_000)
        self.assertEqual(report["final"]["realized_pnl"], 386_000)

    def test_intent_is_not_fill_and_rejection_is_recorded(self):
        before = self.session.observe()
        intent = self.session.submit(Intent("x", "AAPL", "buy", 10, 100 * U), 1)
        after = self.session.observe()
        self.assertEqual(
            (after.cash, after.positions, after.orders),
            (before.cash, before.positions, before.orders),
        )
        self.executor.reject(intent, "fixture rejection", 2)
        event = self.app.events(self.profile, self.account)[-1]
        self.assertEqual(event["data"]["reason"], "fixture rejection")
        with self.assertRaises(ContractError):
            self.executor.accept(intent, "bad", 3)

    def test_multi_account_and_profile_ownership(self):
        other = self.app.create_account(self.profile, "B", 123 * U)
        stranger = self.app.create_profile("Stranger")
        self.buy()
        self.assertEqual(self.app.snapshot(self.profile, other).cash, 123 * U)
        for action in (
            lambda: self.app.snapshot(stranger, self.account),
            lambda: self.app.events(stranger, self.account),
            lambda: self.app.run(stranger, self.run),
            lambda: self.app.create_run(stranger, other, self.maker, ["AAPL"]),
        ):
            with self.assertRaises(ContractError):
                action()

    def test_single_instruction_authority(self):
        other = self.app.create_maker(self.profile, "Human", "human", "v1")
        with self.assertRaises(ContractError):
            self.app.decision_session(self.profile, self.run, other)
        with self.assertRaises(ContractError):
            self.app.create_run(self.profile, self.account, other, ["AAPL"])
        self.app.finish_run(self.profile, self.run, "stopped")
        with self.assertRaises(ContractError):
            self.session.submit(Intent("late", "AAPL", "buy", 1, U), 1)
        with self.assertRaises(ContractError):
            self.session.save_state({})

    def test_immutable_observation_and_policy_state_separation(self):
        before = self.session.observe()
        with self.assertRaises(FrozenInstanceError):
            before.cash = 0
        self.assertFalse(hasattr(self.session, "fill"))
        value = {"memory": [1, 2], "cash": 0}
        self.session.save_state(value)
        value["memory"].append(3)
        loaded = self.session.load_state()
        loaded["memory"].append(4)
        self.assertEqual(self.session.load_state(), {"memory": [1, 2], "cash": 0})
        self.assertEqual(self.session.observe(), before)
        self.app.close()
        self.app = Platform(self.directory)
        self.assertEqual(
            self.app.run(self.profile, self.run)["policy_state"], {"memory": [1, 2], "cash": 0}
        )

    def test_partial_fill_and_cost_allocation(self):
        intent = self.session.submit(Intent("x", "AAPL", "buy", 3, 10), 1)
        self.executor.accept(intent, "x", 2)
        self.executor.fill("a", "x", 1, 10, 1, 3)
        self.assertEqual(self.session.observe().orders[0].status, "partial")
        self.executor.fill("b", "x", 2, 10, 0, 4)
        self.assertEqual(self.session.observe().positions[0].cost, 31)
        sell = self.session.submit(Intent("s", "AAPL", "sell", 3, 20), 5)
        self.executor.accept(sell, "s", 6)
        self.executor.fill("c", "s", 1, 20, 1, 7)
        self.assertEqual(self.session.observe().realized_pnl, 9)  # 20 - 1 - floor(31/3)
        self.assertEqual(self.session.observe().positions[0].cost, 21)
        self.executor.fill("d", "s", 2, 20, 1, 8)
        result = self.session.observe()
        self.assertEqual(result.positions, ())
        self.assertEqual(result.realized_pnl, 27)  # 60 - all 3 fees - 30 acquisition
        self.assertEqual(result.cash, 10_000 * U + 27)

    def test_duplicate_retry_and_conflicting_ids(self):
        self.buy()
        before = self.session.observe()
        self.executor.fill("buy", "buy", 10, 100 * U, U, 12)
        self.assertEqual(self.session.observe(), before)
        self.assertEqual(
            self.session.submit(Intent("buy", "AAPL", "buy", 10, 100 * U), 10),
            f"{self.run}:intent:buy",
        )
        with self.assertRaises(ContractError):
            self.executor.fill("buy", "buy", 10, 100 * U, 0, 12)
        with self.assertRaises(ContractError):
            self.session.submit(Intent("buy", "AAPL", "buy", 11, 100 * U), 10)
        self.assertEqual(self.session.observe(), before)

    def test_invalid_inputs_do_not_write(self):
        before = self.app.events(self.profile, self.account)
        cases = [
            Intent("x", "AAPL", "buy", 0, U),
            Intent("x", "AAPL", "buy", True, U),
            Intent("x", "AAPL", "buy", 1, 1.1),
            Intent("x", "AAPL", "other", 1, U),
            Intent("x", "aapl", "buy", 1, U),
            Intent("x", "MSFT", "buy", 1, U),
            Intent("x", "AAPL", "buy", 2**62, 10),
        ]
        for intent in cases:
            with self.assertRaises(ContractError):
                self.session.submit(intent, 1)
        with self.assertRaises(ContractError):
            self.session.save_state({"value": float("nan")})
        self.assertEqual(self.app.events(self.profile, self.account), before)

    def test_fill_constraints_and_atomic_failure(self):
        intent = self.session.submit(Intent("x", "AAPL", "buy", 1000, 100 * U), 1)
        self.executor.accept(intent, "x", 2)
        before = self.session.observe()
        for quantity, price, fee in [
            (1001, 100 * U, 0),
            (1000, 100 * U, 0),
            (1, 101 * U, 0),
            (0, U, 0),
            (1, U, -1),
            (1, True, 0),
        ]:
            with self.assertRaises(ContractError):
                self.executor.fill("bad", "x", quantity, price, fee, 3)
            self.assertEqual(self.session.observe(), before)
        with self.assertRaises(ContractError):
            self.executor.fill("bad", "unknown", 1, U, 0, 3)

    def test_short_sale_rejected(self):
        intent = self.session.submit(Intent("s", "AAPL", "sell", 1, U), 1)
        self.executor.accept(intent, "s", 2)
        before = self.session.observe()
        with self.assertRaises(ContractError):
            self.executor.fill("s", "s", 1, U, 0, 3)
        self.assertEqual(self.session.observe(), before)

    def test_time_order_and_cross_run_intent(self):
        self.buy()
        with self.assertRaises(ContractError):
            self.session.submit(Intent("past", "AAPL", "buy", 1, U), 1)
        other = self.app.create_account(self.profile, "Other", U)
        run = self.app.create_run(self.profile, other, self.maker, ["AAPL"])
        fixture = FixtureExecutor(self.app, self.profile, run)
        with self.assertRaises(ContractError):
            fixture.accept(f"{self.run}:intent:buy", "foreign", 13)
        intent = self.session.submit(Intent("pending", "AAPL", "buy", 1, U), 20)
        with self.assertRaises(ContractError):
            self.executor.accept(intent, "past-order", 19)

    def test_run_baseline_metadata_and_no_silent_reset(self):
        self.buy()
        baseline = self.app.run(self.profile, self.run)
        self.assertEqual(baseline["initial_snapshot"]["cash"], 10_000 * U)
        self.assertEqual(baseline["decision_maker"]["version"], "v1")
        self.app.finish_run(self.profile, self.run)
        run = self.app.create_run(self.profile, self.account, self.maker, ["AAPL"])
        self.assertEqual(self.app.run(self.profile, run)["initial_snapshot"]["cash"], 8999 * U)
        self.assertEqual(self.app.run(self.profile, run)["policy_state"], {})

    def test_unfinished_run_cannot_be_completed(self):
        intent = self.session.submit(Intent("x", "AAPL", "buy", 1, U), 1)
        with self.assertRaises(ContractError):
            self.app.finish_run(self.profile, self.run)
        self.executor.accept(intent, "x", 2)
        with self.assertRaises(ContractError):
            self.app.finish_run(self.profile, self.run)
        self.app.finish_run(self.profile, self.run, "failed")
        with self.assertRaises(ContractError):
            self.app.create_run(self.profile, self.account, self.maker, ["AAPL"])

    def test_replay_rejects_gaps_duplicates_wrong_account_and_unknown_event(self):
        self.buy()
        events = self.app.events(self.profile, self.account)
        self.assertEqual(
            self.app.replay_snapshot(self.profile, self.account, events), self.session.observe()
        )
        variants = []
        gap = copy.deepcopy(events)
        gap[1]["sequence"] += 1
        variants.append(gap)
        dup = copy.deepcopy(events)
        dup[1]["event_id"] = dup[0]["event_id"]
        variants.append(dup)
        wrong = copy.deepcopy(events)
        wrong[0]["account_id"] = "foreign"
        variants.append(wrong)
        unknown = copy.deepcopy(events)
        unknown[1]["kind"] = "unknown"
        variants.append(unknown)
        past = copy.deepcopy(events)
        past[-1]["time_ns"] = 0
        variants.append(past)
        for variant in variants:
            with self.assertRaises(ContractError):
                self.app.replay_snapshot(self.profile, self.account, variant)

    def test_storage_failure_rolls_back_event_and_run_clock(self):
        intent = self.session.submit(Intent("x", "AAPL", "buy", 1, U), 1)
        self.executor.accept(intent, "x", 2)
        before = self.session.observe()
        with closing(sqlite3.connect(self.directory / "platform.sqlite")) as db, db:
            db.execute(
                "CREATE TRIGGER fail_clock BEFORE UPDATE ON runs BEGIN SELECT RAISE(ABORT, 'injected failure'); END"
            )
        with self.assertRaises(IntegrityError):
            self.executor.fill("x", "x", 1, U, 0, 3)
        self.assertEqual(self.session.observe(), before)
        self.assertEqual(self.app.run(self.profile, self.run)["time_ns"], 2)
        with closing(sqlite3.connect(self.directory / "platform.sqlite")) as db, db:
            db.execute("DROP TRIGGER fail_clock")
        self.executor.fill("x", "x", 1, U, 0, 3)
        self.assertEqual(self.session.observe().cash, 9999 * U)

    def test_process_exit_during_transaction(self):
        code = """from sim_platform import Platform
from sim_platform.persistence.sql.models import ProfileRecord
import os, sys
a = Platform(sys.argv[1])
with a._database.unit_of_work(write=True) as session:
    session.add(ProfileRecord(id='uncommitted', name='bad'))
    session.flush()
    os._exit(23)
"""
        process = subprocess.run([sys.executable, "-c", code, str(self.directory)], cwd=ROOT)
        self.assertEqual(process.returncode, 23)
        self.assertNotIn("uncommitted", [p["id"] for p in self.app.profiles()])
        self.assertEqual(self.session.observe().cash, 10_000 * U)

    def test_concurrent_duplicate_fill_applied_once(self):
        intent = self.session.submit(Intent("x", "AAPL", "buy", 1, U), 1)
        self.executor.accept(intent, "x", 2)

        def apply():
            with Platform(self.directory) as app:
                FixtureExecutor(app, self.profile, self.run).fill("x", "x", 1, U, 0, 3)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(apply) for _ in range(2)]
            for future in futures:
                future.result()
        self.assertEqual(self.session.observe().cash, 9999 * U)
        self.assertEqual(self.session.observe().positions[0].quantity, 1)

    def test_schema_version_and_foreign_database_refused(self):
        for mode in ("future", "foreign"):
            directory = self.directory / mode
            directory.mkdir()
            with closing(sqlite3.connect(directory / "platform.sqlite")) as db, db:
                if mode == "future":
                    db.execute("PRAGMA user_version = 99")
                else:
                    db.execute("CREATE TABLE unrelated (id INTEGER)")
            before = (directory / "platform.sqlite").read_bytes()
            with self.assertRaises(ContractError):
                Platform(directory)
            self.assertEqual((directory / "platform.sqlite").read_bytes(), before)

    @unittest.skipIf(os.name == 'nt', 'the expected paths are POSIX paths')
    def test_paths_are_independent_of_working_directory(self):
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("sys.platform", "darwin"),
            patch("pathlib.Path.home", return_value=Path("/example/user")),
        ):
            self.assertEqual(
                data_directory(), Path("/example/user/Library/Application Support/MarketLab")
            )
        with (
            patch.dict(os.environ, {"LOCALAPPDATA": "/windows/local"}, clear=True),
            patch("sys.platform", "win32"),
        ):
            self.assertEqual(data_directory(), Path("/windows/local/MarketLab"))
        with (
            patch.dict(os.environ, {"XDG_DATA_HOME": "/linux/data"}, clear=True),
            patch("sys.platform", "linux"),
        ):
            self.assertEqual(data_directory(), Path("/linux/data/MarketLab"))
        with patch.dict(os.environ, {"MARKET_LAB_DATA_DIR": "relative"}):
            with self.assertRaises(ValueError):
                data_directory()
        with self.assertRaises(ContractError):
            Platform("relative")

    def test_cli_default_demo_uses_temporary_storage(self):
        directory = self.directory / "should-not-be-created"
        env = {**os.environ, "MARKET_LAB_DATA_DIR": str(directory)}
        for command in ("path", "demo"):
            process = subprocess.run(
                [sys.executable, "-m", "sim_platform", command],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
            )
            self.assertEqual(process.returncode, 0, process.stderr)
            result = json.loads(process.stdout)
            if command == "demo":
                self.assertTrue(result["temporary_data_removed"])
                self.assertEqual(result["result"], "passed")
        self.assertFalse(directory.exists())

    def test_cli_persistent_workflow_and_exact_money(self):
        directory = self.directory / "cli"

        def call(*args, success=True):
            process = subprocess.run(
                [sys.executable, "-m", "sim_platform", "--data-dir", str(directory), *args],
                cwd=ROOT,
                text=True,
                capture_output=True,
            )
            if success:
                self.assertEqual(process.returncode, 0, process.stderr)
                return json.loads(process.stdout)
            self.assertNotEqual(process.returncode, 0)
            return None

        profile = call("create-profile", "CLI")["profile_id"]
        account = call(
            "create-account", "--profile", profile, "--name", "A", "--cash", "10000.0001"
        )["account_id"]
        maker = call(
            "create-maker",
            "--profile",
            profile,
            "--name",
            "Human",
            "--kind",
            "human",
            "--version",
            "1",
        )["maker_id"]
        run = call(
            "create-run",
            "--profile",
            profile,
            "--account",
            account,
            "--maker",
            maker,
            "--symbols",
            "AAPL",
        )["run_id"]
        call(
            "submit",
            "--profile",
            profile,
            "--run",
            run,
            "--maker",
            maker,
            "--client-id",
            "x",
            "--symbol",
            "AAPL",
            "--side",
            "buy",
            "--quantity",
            "1",
            "--limit",
            "100.1234",
            "--time-ns",
            "1",
        )
        snapshot = call("snapshot", "--profile", profile, "--account", account)
        self.assertEqual(snapshot["cash"], 100_000_001)
        self.assertEqual(snapshot["positions"], [])
        self.assertEqual(call("replay", "--profile", profile, "--account", account), snapshot)
        self.assertEqual(call("makers", "--profile", profile)[0]["id"], maker)
        self.assertEqual(call("runs", "--profile", profile)[0]["id"], run)
        for amount in ("0.00001", "NaN", "Infinity", "-1"):
            call(
                "create-account",
                "--profile",
                profile,
                "--name",
                "Bad",
                "--cash",
                amount,
                success=False,
            )
        self.assertEqual(len(call("accounts", "--profile", profile)), 1)
        call("finish-run", "--profile", profile, "--run", run, "--status", "stopped")

    def test_maximum_client_id_can_be_recorded(self):
        intent = self.session.submit(Intent("x" * 256, "AAPL", "buy", 1, U), 1)
        self.executor.accept(intent, "o" * 256, 2)
        self.executor.fill("f" * 256, "o" * 256, 1, U, 0, 3)
        self.assertEqual(self.session.observe().cash, 9999 * U)


if __name__ == "__main__":
    unittest.main()
