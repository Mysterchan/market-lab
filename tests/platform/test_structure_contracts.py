"""Runtime/DB parity, committed publication and production responsibility boundaries."""

import ast
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from sim_platform import Platform, ContractError, Intent
from sim_platform.decisions import HumanPolicy, DecisionConfig, BuyHoldSell
from sim_platform.examples.market_demo import write_fixture
from sim_platform.execution.service import ExecutionService
from sim_platform.persistence.export_files import atomic_write
from sim_platform.research.views import session_view

ROOT = Path(__file__).resolve().parents[2]


class StructureContracts(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        source, _ = write_fixture(self.root / 'fixture')
        self.app = Platform(self.root / 'db')
        self.addCleanup(self.app.close)
        self.profile = self.app.create_profile('Owner')
        self.account = self.app.create_account(self.profile, 'Account', 100_000_000)
        self.maker = self.app.create_maker(self.profile, 'Human', 'human', '1')
        self.market = self.app.open_market()
        self.market.load(source)

    def execution(self):
        run = self.app.create_execution_run(self.profile, self.account, self.maker, 'AAPL')
        owner = self.app.execution_session(self.profile, run, self.maker, self.market)
        for _ in range(3):
            owner.step()
        return run, owner

    def test_event_and_summary_limits_match_live_and_database(self):
        run, owner = self.execution()
        for invalid in (0, -1, True, '1'):
            for api in (self.app._operations, self.app._db_operations):
                for method in ('run_events', 'decision_summaries'):
                    with self.subTest(invalid=invalid, api=type(api).__name__, method=method):
                        with self.assertRaises(ContractError):
                            getattr(api, method)(self.profile, run, limit=invalid)
        self.assertEqual(
            self.app.run_events(self.profile, run, limit=2),
            self.app._db_operations.run_events(self.profile, run, limit=2),
        )

    def test_large_summary_limit_does_not_silently_truncate_live_tail(self):
        policy = HumanPolicy()
        run = self.app.create_decision_run(
            self.profile, self.account, self.maker, 'AAPL', policy, DecisionConfig(mode='manual')
        )
        runner = self.app.decision_runner(self.profile, run, self.maker, self.market, policy)
        for _ in range(105):
            runner.act([])
        live = self.app._operations.decision_summaries(self.profile, run, limit=150)
        self.assertEqual(len(live), 105)
        self.assertEqual(
            live, self.app._db_operations.decision_summaries(self.profile, run, limit=150)
        )

    def test_failure_before_commit_never_publishes_runtime_or_order(self):
        run, owner = self.execution()
        before = owner.snapshot
        position = owner.replay_state
        unit = self.app._db_operations._unit_of_work

        @contextmanager
        def fail_commit(*, write=False):
            with unit(write=write) as session:
                yield session
                if write:
                    raise OSError('commit failure')

        with patch.object(self.app._db_operations, '_unit_of_work', fail_commit):
            with self.assertRaisesRegex(OSError, 'commit failure'):
                owner.submit(Intent('failed', 'AAPL', 'buy', 1, 1_901_000))
        self.assertEqual(owner.snapshot, before)
        self.assertEqual(owner.replay_state, position)
        self.assertEqual(self.app.snapshot(self.profile, self.account), before.account)
        self.assertFalse(any(e['kind'] == 'intent' for e in self.app.run_events(self.profile, run)))

    def test_result_preparation_failure_rolls_back_before_publication(self):
        run, owner = self.execution()
        before = owner.snapshot
        with patch.object(ExecutionService, 'publication', side_effect=OSError('capture failure')):
            with self.assertRaisesRegex(OSError, 'capture failure'):
                owner.submit(Intent('failed', 'AAPL', 'buy', 1, 1_901_000))
        self.assertEqual(owner.snapshot, before)
        self.assertEqual(self.app.snapshot(self.profile, self.account), before.account)

    def test_publication_runs_after_another_connection_can_read_the_commit(self):
        run, owner = self.execution()
        runtime = self.app._resources.runtime(run)
        update = runtime.update
        observed = []

        def publish(data):
            with Platform(self.root / 'db') as other:
                observed.append(other.snapshot(self.profile, self.account))
            update(data)

        with patch.object(runtime, 'update', publish):
            owner.submit(Intent('committed', 'AAPL', 'buy', 1, 1_901_000))
        self.assertEqual(observed, [owner.snapshot.account])
        self.assertEqual(len(owner.snapshot.account.orders), 1)

    def test_live_view_queries_are_inside_public_control_boundary(self):
        controller = self.app.open_research()
        source, _ = write_fixture(self.root / 'other_fixture')
        controller.load(source)
        controller.start(self.profile, self.account, 'human', execution_mode='controlled')
        entered = []
        boundary = controller.runner.control_boundary

        @contextmanager
        def guard():
            with boundary():
                entered.append(True)
                try:
                    yield
                finally:
                    entered.pop()

        read = controller.app.run

        def checked(*args, **kwargs):
            self.assertTrue(entered, 'live run read outside view boundary')
            return read(*args, **kwargs)

        with (
            patch.object(controller.runner, 'control_boundary', guard),
            patch.object(controller.app, 'run', checked),
        ):
            view = controller.view()
        self.assertEqual(view['market']['sequence'], view['execution']['market_sequence'])
        detached = session_view(dict(view))
        detached['market']['sequence'] = -1
        self.assertEqual(view['market']['sequence'], controller.market.snapshot.sequence)

    def test_finished_and_closed_schedules_release_policy_bindings(self):
        from sim_platform.decisions.policy.scheduling import HandoffSchedule

        schedule = HandoffSchedule()
        schedule.register({'id': 'done'}, self.maker, BuyHoldSell(), DecisionConfig(), 'reset')
        schedule.register({'id': 'pending'}, self.maker, BuyHoldSell(), DecisionConfig(), 'reset')
        schedule.reconcile(
            {
                'switches': [
                    {'id': 'done', 'status': 'fired'},
                    {'id': 'pending', 'status': 'pending'},
                ]
            }
        )
        self.assertEqual(set(schedule._policies), {'pending'})
        schedule.clear()
        self.assertEqual(schedule._policies, {})

    def test_atomic_writer_keeps_previous_file_and_removes_temporary_on_serialization_failure(self):
        path = self.root / 'report.json'
        path.write_text('previous')

        def fail(stream):
            stream.write('partial')
            raise OSError('serializer failed')

        with self.assertRaisesRegex(OSError, 'serializer failed'):
            atomic_write(path, fail)
        self.assertEqual(path.read_text(), 'previous')
        self.assertEqual(list(self.root.glob('.report.json*.tmp')), [])

    def test_production_layers_have_no_sql_or_orm_bypass(self):
        for path in (ROOT / 'sim_platform').rglob('*.py'):
            relative = path.relative_to(ROOT / 'sim_platform')
            if relative.parts[0] in ('persistence', 'cli'):
                continue  # CLI left unchanged, including its existing exception adapter.
            for node in ast.walk(ast.parse(path.read_text())):
                imports = (
                    [node.module or '']
                    if isinstance(node, ast.ImportFrom)
                    else [item.name for item in node.names] if isinstance(node, ast.Import) else []
                )
                for name in imports:
                    self.assertFalse(
                        name.startswith(
                            ('sqlite3', 'sqlalchemy', 'sim_platform.persistence.sql.models')
                        ),
                        f'{relative}:{getattr(node, "lineno", 0)} {name}',
                    )
                if isinstance(node, ast.Attribute) and relative.parts[0] in (
                    'application',
                    'execution',
                    'decisions',
                    'research',
                ):
                    self.assertNotEqual(node.attr, 'session', f'{relative}:{node.lineno}')
        operations = (ROOT / 'sim_platform/application/operations.py').read_text()
        for forbidden in (
            '_runtimes',
            'register_runtime',
            'runtime.update',
            '_account_dirty',
            'monotonic',
        ):
            self.assertNotIn(forbidden, operations)
        views = (ROOT / 'sim_platform/research/views.py').read_text()
        for forbidden in ('session.app', 'report_view(', 'session.market', 'session.runner'):
            self.assertNotIn(forbidden, views)


if __name__ == '__main__':
    unittest.main()
