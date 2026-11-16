"""Sparse persistence must survive process exit and replay without original policies."""

from contextlib import closing
import json
import sqlite3
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))
from sim_platform import Platform, Intent, ContractError
from sim_platform.decisions import HumanPolicy, BuyHoldSell, DecisionConfig, submit
from sim_platform.examples.market_demo import write_fixture
from sim_platform.market import load_news

replay_run = Platform.replay_run


class SparseReplayTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source, self.news = write_fixture(self.root / 'fixture')
        self.directory = self.root / 'original'

    def test_idle_checkpoint_waits_thirty_wall_seconds_and_updates_same_row(self):
        from support.execution import ExecutionFixture
        from itch_fixtures import reduce

        case = ExecutionFixture()
        try:
            tail = b''.join(reduce('X', 1, 1, timestamp=stamp) for stamp in range(4, 9))
            with patch('sim_platform.execution.state.monotonic', return_value=0) as clock:
                owner, _, run, _ = case.scenario(tail=tail)

                def durable():
                    with closing(sqlite3.connect(case.app.directory / 'platform.sqlite')) as db, db:
                        rows = db.execute(
                            'SELECT state FROM run_checkpoints WHERE run_id=?', (run,)
                        ).fetchall()
                    self.assertLessEqual(len(rows), 1)
                    return json.loads(rows[0][0]) if rows else None

                self.assertIsNone(durable())
                for now in (1, 29.999):
                    clock.return_value = now
                    owner.step()
                    self.assertIsNone(durable())
                clock.return_value = 30
                owner.step()
                self.assertEqual(durable()['market_sequence'], 6)
                clock.return_value = 59.999
                owner.step()
                self.assertEqual(owner.market_snapshot.sequence, 7)
                self.assertEqual(durable()['market_sequence'], 6)
                clock.return_value = 60
                owner.step()
                self.assertEqual(durable()['market_sequence'], 8)
                owner.close()
        finally:
            case.close()

    def test_fill_pause_and_stop_save_before_periodic_deadline(self):
        from support.execution import ExecutionFixture

        case = ExecutionFixture()
        try:
            with patch('time.monotonic', return_value=0):
                owner, _, run, account = case.scenario()
                owner.submit(Intent('buy', 'AAPL', 'buy', 1, 1_001_000))
                owner.step()
                with closing(sqlite3.connect(case.app.directory / 'platform.sqlite')) as db, db:
                    self.assertEqual(
                        db.execute(
                            "SELECT COUNT(*) FROM events WHERE run_id=? AND kind='fill'", (run,)
                        ).fetchone()[0],
                        1,
                    )
                    state = json.loads(
                        db.execute(
                            'SELECT state FROM run_checkpoints WHERE run_id=?', (run,)
                        ).fetchone()[0]
                    )
                    self.assertEqual(state['market_sequence'], 4)
                owner.step()
                owner.play()
                owner.pause()
                with closing(sqlite3.connect(case.app.directory / 'platform.sqlite')) as db, db:
                    state = json.loads(
                        db.execute(
                            'SELECT state FROM run_checkpoints WHERE run_id=?', (run,)
                        ).fetchone()[0]
                    )
                    self.assertEqual(state['market_sequence'], 5)
                owner.step()
                owner.close()
                with closing(sqlite3.connect(case.app.directory / 'platform.sqlite')) as db, db:
                    state = json.loads(
                        db.execute(
                            'SELECT state FROM run_checkpoints WHERE run_id=?', (run,)
                        ).fetchone()[0]
                    )
                    self.assertEqual(state['market_sequence'], 6)
                    self.assertEqual(
                        db.execute('SELECT status FROM runs WHERE id=?', (run,)).fetchone()[0],
                        'stopped',
                    )
        finally:
            case.close()

    def run_case(self, mode='manual', handover=False):
        policy = HumanPolicy() if mode == 'manual' else BuyHoldSell()
        with Platform(self.directory) as app:
            profile = app.create_profile('Owner')
            account = app.create_account(profile, 'Account', 100_000_000)
            maker = app.create_maker(profile, policy.name, policy.kind, policy.version)
            run = app.create_decision_run(
                profile, account, maker, 'AAPL', policy, DecisionConfig(mode=mode)
            )
            with app.open_market() as market:
                market.load(self.source, news=load_news(self.news))
                with app.decision_runner(profile, run, maker, market, policy) as runner:
                    if mode == 'manual':
                        for _ in range(3):
                            runner.step()
                        runner.act([], state={'memory': 42})
                        runner.act([submit(Intent('buy', 'AAPL', 'buy', 10, 1_901_000))])
                    if handover:
                        other = BuyHoldSell()
                        target = app.create_maker(profile, other.name, other.kind, other.version)
                        runner.handoff(target, other)
                    while runner.snapshot.status == 'running':
                        runner.step()
            return run

    def test_sparse_replay_after_original_process_state_is_gone(self):
        run = self.run_case()
        before = (self.directory / 'platform.sqlite').read_bytes()
        result = replay_run(self.directory, run, self.root / 'replay')
        self.assertTrue(result['verified'])
        self.assertEqual(result['decisions'], 2)
        self.assertEqual((self.directory / 'platform.sqlite').read_bytes(), before)
        with closing(sqlite3.connect(self.directory / 'platform.sqlite')) as db, db:
            self.assertEqual(
                db.execute("SELECT count(*) FROM events WHERE kind='execution_tick'").fetchone()[0],
                1,
            )

    def test_no_directory_selects_the_default_data_directory(self):
        run = self.run_case()
        with patch.dict('os.environ', MARKET_LAB_DATA_DIR=str(self.directory)):
            result = replay_run(None, run, self.root / 'replay')
        self.assertTrue(result['verified'])

    def test_automatic_and_handover_replay(self):
        run = self.run_case(handover=True)
        self.assertTrue(replay_run(self.directory, run, self.root / 'replay')['verified'])

    def test_checkpoint_is_not_required_for_full_replay(self):
        run = self.run_case(mode='automatic')
        with closing(sqlite3.connect(self.directory / 'platform.sqlite')) as db, db:
            db.execute('DELETE FROM run_checkpoints')
        self.assertTrue(replay_run(self.directory, run, self.root / 'replay')['verified'])

    def test_original_policy_cannot_be_invoked(self):
        run = self.run_case(mode='automatic')
        with patch.object(
            BuyHoldSell, 'decide', side_effect=AssertionError('original policy invoked')
        ):
            self.assertTrue(replay_run(self.directory, run, self.root / 'replay')['verified'])

    def test_source_corruption_is_rejected(self):
        run = self.run_case()
        self.source.write_bytes(self.source.read_bytes() + b'corrupt')
        with self.assertRaisesRegex(ContractError, 'digest'):
            replay_run(self.directory, run, self.root / 'replay')

    def test_idle_frames_do_not_open_database_transactions(self):
        with Platform(self.directory) as app:
            profile = app.create_profile('Owner')
            account = app.create_account(profile, 'Account', 100_000_000)
            maker = app.create_maker(profile, 'Human', 'human', '1')
            run = app.create_execution_run(profile, account, maker, 'AAPL')
            with app.open_market() as market:
                market.load(self.source)
                with app.execution_session(profile, run, maker, market) as owner:
                    with patch.object(
                        app._db_operations, '_unit_of_work', side_effect=AssertionError('idle SQL')
                    ):
                        for _ in range(3):
                            owner.step()
                        self.assertEqual(owner.snapshot.account.cash, 100_000_000)

    def test_actual_subprocess_replay(self):
        run = self.run_case(mode='automatic')
        code = "from sim_platform import Platform; replay_run = Platform.replay_run; import sys,json; print(json.dumps(replay_run(*sys.argv[1:])))"
        result = subprocess.run(
            [sys.executable, '-c', code, str(self.directory), run, str(self.root / 'child-replay')],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)['verified'])

    def test_direct_execution_partial_fill_and_cancel_replay(self):
        from support.execution import ExecutionFixture

        case = ExecutionFixture()
        try:
            owner, market, run, _ = case.scenario()
            result = owner.submit(Intent('partial', 'AAPL', 'buy', 15, 1_001_000))
            owner.step()
            self.assertEqual(owner.snapshot.account.orders[-1].filled, 10)
            owner.cancel(result.order_id)
            with self.assertRaises(ContractError):
                owner.cancel('unknown')
            case.drain(owner)
            case.app.close()
            self.assertTrue(
                replay_run(case.path / 'accounts', run, self.root / 'direct-replay')['verified']
            )
        finally:
            case.close()

    def test_model_and_agent_empty_actions_replay(self):
        from sim_platform.decisions import JSONProcessPolicy

        for kind in ('model', 'agent'):
            directory = self.root / kind
            policy = JSONProcessPolicy(
                [
                    sys.executable,
                    '-c',
                    'import json; print(json.dumps({"actions":[],"state":{"memory":42}}))',
                ],
                kind=kind,
            )
            with Platform(directory) as app:
                profile = app.create_profile('Owner')
                account = app.create_account(profile, 'Account', 100_000_000)
                maker = app.create_maker(profile, policy.name, kind, policy.version)
                run = app.create_decision_run(profile, account, maker, 'AAPL', policy)
                with app.open_market() as market:
                    market.load(self.source)
                    with app.decision_runner(profile, run, maker, market, policy) as owner:
                        while owner.snapshot.status == 'running':
                            owner.step()
            self.assertTrue(replay_run(directory, run, self.root / (kind + '-replay'))['verified'])

    def test_failed_model_decision_replay(self):
        from sim_platform.decisions import JSONProcessPolicy

        policy = JSONProcessPolicy([sys.executable, '-c', 'print("invalid json")'])
        with Platform(self.directory) as app:
            profile = app.create_profile('Owner')
            account = app.create_account(profile, 'Account', 100_000_000)
            maker = app.create_maker(profile, policy.name, policy.kind, policy.version)
            run = app.create_decision_run(profile, account, maker, 'AAPL', policy)
            with app.open_market() as market:
                market.load(self.source)
                with app.decision_runner(profile, run, maker, market, policy) as owner:
                    owner.step()
                    self.assertEqual(owner.snapshot.status, 'failed')
        self.assertTrue(replay_run(self.directory, run, self.root / 'failed-replay')['verified'])

    def test_scheduled_handover_and_play_replay(self):
        policy = HumanPolicy()
        with Platform(self.directory) as app:
            profile = app.create_profile('Owner')
            account = app.create_account(profile, 'Account', 100_000_000)
            maker = app.create_maker(profile, policy.name, policy.kind, policy.version)
            other = BuyHoldSell()
            target = app.create_maker(profile, other.name, other.kind, other.version)
            run = app.create_decision_run(
                profile, account, maker, 'AAPL', policy, DecisionConfig(mode='manual')
            )
            with app.open_market() as market:
                market.load(self.source, news=load_news(self.news))
                with app.decision_runner(profile, run, maker, market, policy) as owner:
                    owner.schedule_handoff(target, other, {'kind': 'time', 'time_ns': 3})
                    owner.play()
                    while owner.snapshot.status == 'running':
                        owner.pump(1)
        self.assertTrue(replay_run(self.directory, run, self.root / 'scheduled-replay')['verified'])

    def test_timeout_replays_without_waiting_for_original_model(self):
        from sim_platform.decisions import JSONProcessPolicy

        policy = JSONProcessPolicy([sys.executable, '-c', 'import time; time.sleep(10)'])
        with Platform(self.directory) as app:
            profile = app.create_profile('Owner')
            account = app.create_account(profile, 'Account', 100_000_000)
            maker = app.create_maker(profile, policy.name, policy.kind, policy.version)
            run = app.create_decision_run(
                profile, account, maker, 'AAPL', policy, DecisionConfig(timeout_ms=50)
            )
            with app.open_market() as market:
                market.load(self.source)
                with app.decision_runner(profile, run, maker, market, policy) as owner:
                    owner.step()
                    self.assertEqual(owner.records[0]['status'], 'timeout')
        with patch.object(JSONProcessPolicy, 'decide', side_effect=AssertionError('model invoked')):
            self.assertTrue(
                replay_run(self.directory, run, self.root / 'timeout-replay')['verified']
            )

    def test_handover_restore_and_reset_replay(self):
        policy, other = HumanPolicy(), BuyHoldSell()
        with Platform(self.directory) as app:
            profile = app.create_profile('Owner')
            account = app.create_account(profile, 'Account', 100_000_000)
            maker = app.create_maker(profile, policy.name, policy.kind, policy.version)
            target = app.create_maker(profile, other.name, other.kind, other.version)
            run = app.create_decision_run(
                profile, account, maker, 'AAPL', policy, DecisionConfig(mode='manual')
            )
            with app.open_market() as market:
                market.load(self.source)
                with app.decision_runner(profile, run, maker, market, policy) as owner:
                    owner.act([], state={'memory': 42})
                    owner.handoff(target, other)
                    owner.handoff(
                        maker, policy, DecisionConfig(mode='manual'), state_mode='restore'
                    )
                    self.assertEqual(app.run(profile, run)['policy_state']['state'], {'memory': 42})
                    owner.handoff(target, other)
                    owner.handoff(maker, policy, DecisionConfig(mode='manual'), state_mode='reset')
                    self.assertEqual(app.run(profile, run)['policy_state']['state'], {})
                    while owner.snapshot.status == 'running':
                        owner.step()
        self.assertTrue(replay_run(self.directory, run, self.root / 'state-replay')['verified'])

    def test_live_owner_cannot_be_recovered_by_another_platform(self):
        with Platform(self.directory) as app:
            profile = app.create_profile('Owner')
            account = app.create_account(profile, 'Account', 100_000_000)
            maker = app.create_maker(profile, 'Human', 'human', '1')
            run = app.create_execution_run(profile, account, maker, 'AAPL')
            with app.open_market() as market:
                market.load(self.source)
                with app.execution_session(profile, run, maker, market):
                    with Platform(self.directory) as other:
                        with self.assertRaisesRegex(ContractError, 'another instance'):
                            other.abort_execution_run(profile, run)

    def test_checkpoint_failure_after_fill_rolls_back_account_and_budget(self):
        from support.execution import ExecutionFixture
        from sim_platform.persistence.sql.repositories import RunRepository

        case = ExecutionFixture()
        try:
            owner, _, run, account = case.scenario()
            owner.submit(Intent('buy', 'AAPL', 'buy', 1, 1_001_000))
            original = RunRepository.save_execution_state
            count = [0]

            def failed(repo, identity, state):
                count[0] += 1
                if count[0] == 1:
                    raise OSError('checkpoint failure after fill')
                return original(repo, identity, state)

            with (
                patch.object(RunRepository, 'save_execution_state', failed),
                self.assertRaises(OSError),
            ):
                owner.step()
            self.assertEqual(owner.snapshot.account.cash, 100_000_000)
            self.assertFalse(
                any(x['kind'] == 'fill' for x in case.app.run_events(case.profile, run))
            )
        finally:
            case.close()

    def test_interrupted_checkpoint_recovers_and_continues(self):
        from support.execution import ExecutionFixture

        case = ExecutionFixture()
        try:
            owner, _, run, _ = case.scenario()
            owner.submit(Intent('partial', 'AAPL', 'buy', 15, 1_001_000))
            owner.step()
            expected = owner.snapshot.account
            owner._unbind()  # simulate a dead process; no execution_end is written
            case.app.close()
            source = case.path / 'accounts'
            before = (source / 'platform.sqlite').read_bytes()
            with replay_run(source, run, self.root / 'restored', recover=True) as recovered:
                self.assertEqual(recovered.owner.snapshot.account, expected)
                self.assertEqual(recovered.market.status, 'paused')
                while recovered.owner.snapshot.status == 'running':
                    recovered.owner.step()
                self.assertEqual(recovered.owner.snapshot.account.orders[-1].filled, 15)
            self.assertEqual((source / 'platform.sqlite').read_bytes(), before)
        finally:
            case.close()

    def test_missing_empty_decision_is_not_silently_accepted(self):
        run = self.run_case()
        with closing(sqlite3.connect(self.directory / 'platform.sqlite')) as db, db:
            db.execute('DELETE FROM decision_records WHERE decision_index=1')
        with self.assertRaisesRegex(ContractError, 'incomplete'):
            replay_run(self.directory, run, self.root / 'replay')

    def test_same_boundary_old_authority_rejection_replays(self):
        with Platform(self.directory) as app:
            profile = app.create_profile('Owner')
            account = app.create_account(profile, 'Account', 100_000_000)
            policy = HumanPolicy()
            maker = app.create_maker(profile, policy.name, policy.kind, policy.version)
            other = HumanPolicy()
            target = app.create_maker(profile, other.name, other.kind, other.version)
            run = app.create_decision_run(
                profile, account, maker, 'AAPL', policy, DecisionConfig(mode='manual')
            )
            with app.open_market() as market:
                market.load(self.source)
                with app.decision_runner(profile, run, maker, market, policy) as owner:
                    for _ in range(3):
                        owner.step()
                    old = owner.controller()
                    owner.act([], state={'memory': 42})
                    owner.handoff(target, other)
                    with self.assertRaises(ContractError):
                        old.act([])
                    owner.act([])
        self.assertTrue(replay_run(self.directory, run, self.root / 'rejected-replay')['verified'])

    def test_human_checkpoint_restores_pending_handover(self):
        with Platform(self.directory) as app:
            profile = app.create_profile('Owner')
            account = app.create_account(profile, 'Account', 100_000_000)
            policy = HumanPolicy()
            maker = app.create_maker(profile, policy.name, policy.kind, policy.version)
            other = BuyHoldSell()
            target = app.create_maker(profile, other.name, other.kind, other.version)
            run = app.create_decision_run(
                profile, account, maker, 'AAPL', policy, DecisionConfig(mode='manual')
            )
            market = app.open_market()
            market.load(self.source, news=load_news(self.news))
            owner = app.decision_runner(profile, run, maker, market, policy)
            for _ in range(3):
                owner.step()
            owner.schedule_handoff(
                target, other, {'kind': 'time', 'time_ns': owner.market_snapshot.time_ns + 1}
            )
            owner.pause()
            expected = owner.snapshot.account
            owner._execution._unbind()
        with replay_run(
            self.directory,
            run,
            self.root / 'pending-restored',
            recover=True,
            policy_bindings={target: other},
        ) as recovered:
            self.assertEqual(recovered.owner.snapshot.account, expected)
            while recovered.owner.snapshot.status == 'running':
                recovered.owner.step()
            self.assertEqual(recovered.owner._generation, 1)
            self.assertIs(recovered.owner._policy, other)

    def test_v5_migration_preserves_existing_bytes_and_rolls_back_failure(self):
        from sim_platform.persistence.sql.migrations import v0006_runtime

        run = self.run_case()
        database = self.directory / 'platform.sqlite'
        with closing(sqlite3.connect(database)) as db, db:
            db.execute('DROP TABLE agent_experiments')
            db.execute('DROP TABLE agent_revisions')
            db.execute('DROP TABLE run_checkpoints')
            db.execute("UPDATE alembic_version SET version_num='0005_projections'")
            db.execute('PRAGMA user_version=5')
            original = {
                table: db.execute('SELECT * FROM ' + table + ' ORDER BY 1').fetchall()
                for table in ('runs', 'events', 'decision_records', 'research_archives')
            }
        before = database.read_bytes()
        upgrade = v0006_runtime.upgrade

        def failed(connection):
            upgrade(connection)
            raise OSError('injected migration failure')

        with patch.object(v0006_runtime, 'upgrade', failed), self.assertRaises(OSError):
            Platform(self.directory)
        self.assertEqual(database.read_bytes(), before)
        with Platform(self.directory):
            pass
        with closing(sqlite3.connect(database)) as db, db:
            for table, rows in original.items():
                self.assertEqual(
                    db.execute('SELECT * FROM ' + table + ' ORDER BY 1').fetchall(), rows
                )
        self.assertTrue(replay_run(self.directory, run, self.root / 'migrated-replay')['verified'])

    def test_invalid_checkpoint_is_rejected(self):
        run = self.run_case()
        with closing(sqlite3.connect(self.directory / 'platform.sqlite')) as db, db:
            state = json.loads(db.execute('SELECT state FROM run_checkpoints').fetchone()[0])
            state['bids'][0][2] = state['bids'][0][1] + 1
            db.execute('UPDATE run_checkpoints SET state=?', (json.dumps(state),))
        with self.assertRaisesRegex(ContractError, 'checkpoint'):
            replay_run(self.directory, run, self.root / 'invalid-replay')


if __name__ == '__main__':
    unittest.main()
