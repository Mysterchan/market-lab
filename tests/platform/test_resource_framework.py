"""Behavioral contracts for platform factories, ownership and non-SQL scratch files."""

from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import unittest
from unittest.mock import patch

from sim_platform import Platform, ContractError
from sim_platform.application.lifecycle import PlatformLifecycle
from sim_platform.examples.market_demo import write_fixture
from sim_platform.market import MarketSession, MarketError
from sim_platform.persistence.workspace.temporary import AppWorkspace
from sim_platform.research.controller import ResearchController


class ResourceFrameworkTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source, self.news = write_fixture(self.root / 'source')

    def test_plain_market_engine_has_no_storage_side_effect(self):
        with (
            patch('sqlite3.connect', side_effect=AssertionError('SQL opened')),
            patch('tempfile.mkdtemp', side_effect=AssertionError('temporary directory opened')),
        ):
            engine = MarketSession()
            engine.close()
        # A failed platform allocation must also release the already-created engine.
        with Platform(self.root / 'data', database=False) as platform:
            with (
                patch.object(platform._workspace, 'market_cache', side_effect=OSError('no space')),
                patch('sim_platform.market.MarketSession') as engine_type,
            ):
                with self.assertRaises(OSError):
                    platform.open_market()
                engine_type.return_value.close.assert_called_once()

    def test_market_only_platform_uses_files_and_removes_them_without_creating_db(self):
        directory = self.root / 'unused-account-directory'
        with Platform(directory, database=False) as platform:
            workspace = platform._workspace.path
            with patch('sqlite3.connect', side_effect=AssertionError('SQL opened')):
                market = platform.open_market()
                market.load(self.source)
                market.play()
                while market.status == 'playing':
                    market.pump(10)
                self.assertEqual(len(market.trades()), 1)  # Broken trade excluded.
                self.assertTrue(list(workspace.rglob('trades')))
                self.assertFalse(directory.exists())
        self.assertFalse(workspace.exists())
        self.assertFalse(directory.exists())
        with self.assertRaises(MarketError):
            market.play()
        with self.assertRaises(ContractError):
            platform.open_market()

    def test_controller_close_does_not_close_shared_platform_or_other_experiment(self):
        with Platform(self.root / 'data') as platform:
            first = platform.open_research()
            second = platform.open_research()
            self.assertIsInstance(first, ResearchController)
            first.load(self.source)
            second.load(self.source)
            first.close()
            second.step()
            profile = platform.create_profile('Still available')
            self.assertEqual(platform.profiles()[0]['id'], profile)
            with self.assertRaises(ContractError):
                first.step()

    def test_platform_close_stops_child_run_and_removes_workspace(self):
        directory = self.root / 'data'
        platform = Platform(directory)
        self.addCleanup(platform.close)
        workspace = platform._workspace.path
        controller = platform.open_research()
        profile = platform.create_profile('Research')
        account = platform.create_account(profile, 'Account', 100_000_000)
        controller.load(self.source)
        controller.start(profile, account, execution_mode='controlled')
        for _ in range(3):
            controller.step()
        controller.order('open', 'buy', 1, 1)
        run = controller.run_id
        platform.close()
        self.assertFalse(workspace.exists())
        with Platform(directory) as reopened:
            self.assertEqual(reopened.run(profile, run)['status'], 'stopped')
            self.assertEqual(reopened.snapshot(profile, account).orders[0].status, 'cancelled')

    def test_failed_candidate_load_keeps_original_market_and_source(self):
        with Platform(self.root / 'data') as platform:
            controller = platform.open_research()
            controller.load(self.source)
            controller.step()
            original, source = controller.market, controller.source.copy()
            position = original.snapshot
            for options in (
                {'path': self.root / 'missing.bin'},
                {'path': self.source, 'news': self.source},
            ):
                with self.assertRaises((OSError, ValueError)):
                    controller.load(**options)
                self.assertIs(controller.market, original)
                self.assertEqual(controller.market.snapshot, position)
                self.assertEqual(controller.source, source)
            controller.step()

    def test_managed_handoff_failure_does_not_commit_new_maker(self):
        with Platform(self.root / 'data') as platform:
            profile = platform.create_profile('Research')
            account = platform.create_account(profile, 'Account', 100_000_000)
            controller = platform.open_research()
            controller.load(self.source)
            controller.start(profile, account, execution_mode='controlled')
            before = platform.makers(profile)
            for condition in (None, {'kind': 'time', 'time_ns': 1}):
                with self.assertRaisesRegex(ContractError, 'compatible saved policy state'):
                    controller.handoff('hold', state_mode='restore', condition=condition)
                self.assertEqual(platform.makers(profile), before)

    def test_changed_prepared_artifact_rejects_reuse_before_creating_financial_run(self):
        with Platform(self.root / 'data') as platform:
            controller = platform.open_research()
            profile = platform.create_profile('Research')
            controller.load(self.source, news=self.news)
            artifact = controller.market.reader.directory
            controller.close()
            path = artifact / 'events.bin'
            path.write_bytes(path.read_bytes() + b'x')
            second = platform.open_research()
            with self.assertRaisesRegex(ContractError, 'length mismatch'):
                second.load(self.source, news=self.news)
            self.assertEqual(platform.runs(profile), [])
            self.assertIsNone(second.market)

    def test_workspace_cleanup_preserves_live_instance_and_removes_stale_files(self):
        root = self.root / 'cache'
        first = AppWorkspace(root)
        self.addCleanup(first.close)
        stale = root / 'workspace-stale'
        stale.mkdir()
        (stale / 'owner.lock').write_bytes(b'')
        (stale / 'data').write_bytes(b'old')
        second = AppWorkspace(root)
        self.addCleanup(second.close)
        self.assertTrue(first.path.exists())
        self.assertFalse(stale.exists())
        second.close()
        self.assertTrue(first.path.exists())

    def test_early_playback_close_removes_only_its_cache(self):
        with Platform(self.root / 'data', database=False) as platform:
            first, second = platform.open_market(), platform.open_market()
            a = first._history._files.directory
            b = second._history._files.directory
            first.close()
            first.close()
            self.assertFalse(a.exists())
            self.assertTrue(b.exists())
            second.load(self.source)
            second.step()

    def test_platform_shutdown_joins_job_before_deleting_worker_resources(self):
        platform = Platform(self.root / 'data')
        self.addCleanup(platform.close)
        started = Event()
        workspace = platform._workspace.path
        job = platform.open_jobs()

        def operation(*, cancellation):
            controller = platform.open_research()
            try:
                profile = controller.create_profile('Worker')
                account = controller.create_account(profile, 'Worker account', 100_000_000)
                controller.load(self.source)
                controller.start(profile, account, execution_mode='controlled')
                started.set()
                cancellation._event.wait(3)
                controller.stop()
                return workspace.exists() and cancellation.cancelled
            finally:
                controller.close()

        job.start(operation)
        self.assertTrue(started.wait(3))
        platform.close()
        self.assertTrue(job.future.result(timeout=1))
        self.assertFalse(workspace.exists())

    def test_batch_factory_runs_independent_accounts_without_owning_the_platform(self):
        with Platform(self.root / 'data') as platform:
            batch = platform.open_batch()
            result = batch.compare(self.source, include_details=False)
            runs = [item['report']['run'] for item in result['results']]
            self.assertEqual(len(runs), 2)
            self.assertNotEqual(runs[0]['id'], runs[1]['id'])
            for run in runs:
                self.assertEqual(platform.run(run['profile_id'], run['id'])['status'], 'completed')
            batch.close()
            platform.create_profile('Platform still open')

    def test_platform_reset_reopen_failure_restores_saved_accounts(self):
        directory = self.root / 'data'
        with Platform(directory) as platform:
            profile = platform.create_profile('Existing')
            account = platform.create_account(profile, 'Keep', 100_000_000)
        original_open = Platform.open_research

        def failed_reopen(platform, **options):
            if platform.directory == directory:
                raise OSError('replacement reopen failed')
            return original_open(platform, **options)

        with PlatformLifecycle(directory) as lifecycle:
            with patch.object(Platform, 'open_research', failed_reopen):
                with self.assertRaisesRegex(OSError, 'replacement reopen'):
                    lifecycle.reset()
            self.assertEqual(lifecycle.open().accounts(profile)[0]['id'], account)
        with Platform(directory) as recovered:
            self.assertEqual(recovered.accounts(profile)[0]['id'], account)


if __name__ == '__main__':
    unittest.main()
