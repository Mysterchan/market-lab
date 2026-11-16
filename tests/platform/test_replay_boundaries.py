"""Replay must retain the same storage and root boundaries as other controllers."""

import ast
from pathlib import Path
from tempfile import TemporaryDirectory
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from sim_platform import Platform, ContractError
from sim_platform.examples.market_demo import write_fixture


class ReplayBoundaryTests(unittest.TestCase):
    def test_replay_has_no_sql_root_creation_or_foreign_private_access(self):
        tree = ast.parse((ROOT / 'sim_platform/replay/runner.py').read_text())
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = (
                    [node.module or '']
                    if isinstance(node, ast.ImportFrom)
                    else [a.name for a in node.names]
                )
                for name in names:
                    self.assertFalse(
                        name.startswith(
                            (
                                'sqlite3',
                                'sqlalchemy',
                                'sim_platform.persistence',
                                'sim_platform.application',
                                'PySide6',
                                'sim_platform.cli',
                                'sim_platform.desktop',
                            )
                        ),
                        name,
                    )
            if isinstance(node, ast.Name):
                self.assertNotEqual(node.id, 'Platform')
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                if node.value.id in ('app', 'owner', 'execution'):
                    self.assertFalse(node.attr.startswith('_'), node.attr)

    def test_execution_service_does_not_operate_on_orm_session(self):
        tree = ast.parse((ROOT / 'sim_platform/execution/service.py').read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                self.assertNotEqual(node.attr, 'session')
            if isinstance(node, ast.ImportFrom):
                self.assertNotEqual(node.module, 'sim_platform.persistence.sql.models')

    def test_market_descriptor_needs_no_hash_or_implementation_files(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            source, _ = write_fixture(root / 'fixture')
            with Platform(root / 'db', database=False) as app:
                with app.open_market() as market:
                    market.load(source)
                    with (
                        patch(
                            'sim_platform.persistence.replay.files.digest',
                            side_effect=AssertionError('hash IO'),
                        ),
                        patch(
                            'sim_platform.persistence.replay.files.implementation_digest',
                            side_effect=AssertionError('rules IO'),
                        ),
                    ):
                        descriptor = market.source_descriptor()
                    self.assertEqual(descriptor['path'], str(source.resolve()))
                    self.assertNotIn('rules_sha256', descriptor)
                    self.assertFalse((root / 'db/platform.sqlite').exists())

    def test_platform_closes_registered_replay_and_rejects_creation_after_close(self):
        with TemporaryDirectory() as temp:
            app = Platform(Path(temp) / 'db', database=False)
            replay = app.open_replay()
            app.close()
            self.assertTrue(replay.closed)
            with self.assertRaises(ContractError):
                app.open_replay()

    def test_legacy_execution_unbind_does_not_require_runtime_cache(self):
        from dataclasses import asdict
        from sim_platform.execution import ExecutionConfig

        with TemporaryDirectory() as temp:
            root = Path(temp)
            source, _ = write_fixture(root / 'fixture')
            with Platform(root / 'db') as app:
                profile = app.create_profile('Owner')
                account = app.create_account(profile, 'Legacy', 100_000_000)
                maker = app.create_maker(profile, 'Human', 'human', '1')
                run = app.create_run(
                    profile,
                    account,
                    maker,
                    ['AAPL'],
                    {'execution': asdict(ExecutionConfig()), 'storage': 'controlled'},
                )
                with app.open_market() as market:
                    market.load(source)
                    with app.execution_session(profile, run, maker, market) as owner:
                        owner.step()
                    self.assertTrue(owner.closed)
                    self.assertFalse(market.has_execution_owner)
                    ticks = [
                        e for e in app.run_events(profile, run) if e['kind'] == 'execution_tick'
                    ]
                    self.assertGreater(len(ticks), 1)


if __name__ == '__main__':
    unittest.main()
