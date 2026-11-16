"""One root per app, borrowed legacy APIs, and reset resource replacement."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from sim_platform import Platform, ContractError
from sim_platform.cli.context import CliContext
from sim_platform.desktop.runtime import DesktopRuntime
from sim_platform.research import ResearchSession
from sim_platform.research.batch import compare, evaluate
from sim_platform.desktop.datasets import DatasetLibrary
from sim_platform.examples.market_demo import write_fixture


class ApplicationLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = write_fixture(self.root / 'source')

    def test_cli_borrows_one_platform_until_invocation_closes(self):
        with CliContext(self.root / 'accounts') as context:
            with context.open_platform(database=False) as first:
                workspace = first._workspace.path
            self.assertFalse(first.closed)
            with context.open_platform() as second:
                self.assertIs(first, second)
                second.create_profile('CLI')
            with self.assertRaises(RuntimeError):
                with context.open_platform() as third:
                    self.assertIs(third, first)
                    raise RuntimeError('handler failure')
            self.assertFalse(first.closed)
            with self.assertRaises(ContractError):
                context.select_directory(self.root / 'different')
        self.assertTrue(first.closed)
        self.assertFalse(workspace.exists())
        with self.assertRaises(ContractError):
            with context.open_platform():
                pass

    def test_legacy_names_borrow_root_instead_of_opening_another(self):
        with Platform(self.root / 'accounts') as platform:
            with patch.object(
                Platform, '__init__', side_effect=AssertionError('second root created')
            ):
                session = ResearchSession(platform)
                session.close()
                self.assertFalse(platform.closed)
                DatasetLibrary(platform).catalog()
                result = evaluate(platform, self.source[0], include_details=False)
                comparison = compare(platform, self.source[0], include_details=False)
                profile = result['run']['profile_id']
                self.assertEqual(platform.run(profile, result['run']['id'])['status'], 'completed')
                for entry in comparison['results']:
                    report = entry['report']
                    self.assertEqual(
                        platform.run(report['run']['profile_id'], report['run']['id'])['status'],
                        'completed',
                    )
        for operation in (
            lambda: ResearchSession(self.root / 'accounts'),
            lambda: DatasetLibrary(self.root / 'accounts'),
            lambda: evaluate(self.root / 'accounts', self.source[0]),
            lambda: compare(self.root / 'accounts', self.source[0]),
        ):
            with self.assertRaisesRegex(ContractError, 'existing Platform'):
                operation()

    def test_desktop_initialization_is_idempotent_and_reset_replaces_all_bindings(self):
        runtime = DesktopRuntime(self.root / 'accounts', demo_source=self.source)
        self.addCleanup(runtime.close)
        runtime.initialize()
        old = runtime.platform
        old_session, old_job, old_batch, old_datasets = (
            runtime.session,
            runtime.job,
            runtime.batch,
            runtime.datasets,
        )
        workspace = old._workspace.path
        runtime.initialize()
        self.assertIs(runtime.platform, old)
        self.assertIs(runtime.session, old_session)
        runtime.reset()
        self.assertTrue(old.closed)
        self.assertTrue(old_session.closed)
        self.assertFalse(workspace.exists())
        self.assertIsNot(runtime.platform, old)
        for before, after in [
            (old_session, runtime.session),
            (old_job, runtime.job),
            (old_batch, runtime.batch),
            (old_datasets, runtime.datasets),
        ]:
            self.assertIsNot(before, after)
        runtime.datasets.catalog()
        self.assertEqual(runtime.batch.evaluate(self.source[0])['summary']['status'], 'completed')
        runtime.close()
        with self.assertRaises(ContractError):
            runtime.platform

    def test_reset_rejection_preserves_active_experiment_and_resources(self):
        runtime = DesktopRuntime(self.root / 'accounts', demo_source=self.source)
        self.addCleanup(runtime.close)
        runtime.initialize()
        platform, controller = runtime.platform, runtime.session
        catalog = controller.catalog()
        controller.start(
            catalog['profile'], catalog['accounts'][0]['id'], execution_mode='controlled'
        )
        with self.assertRaisesRegex(ContractError, 'stop all running'):
            runtime.reset()
        self.assertIs(runtime.platform, platform)
        self.assertIs(runtime.session, controller)
        controller.step()

    def test_initialize_failure_releases_root_and_cache(self):
        runtime = DesktopRuntime(self.root / 'accounts', demo_source=self.source)
        with patch(
            'sim_platform.desktop.runtime.seed_research', side_effect=OSError('seed failed')
        ):
            with self.assertRaisesRegex(OSError, 'seed failed'):
                runtime.initialize()
        self.assertTrue(runtime.lifecycle.closed)
        self.assertTrue(runtime.lifecycle._platform.closed)
        self.assertFalse(runtime.lifecycle._platform._workspace.path.exists())


if __name__ == '__main__':
    unittest.main()
