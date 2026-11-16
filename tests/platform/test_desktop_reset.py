"""Reset failures restore storage and keep the app lifecycle usable, independently of Qt."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from sim_platform.application.lifecycle import PlatformLifecycle
from sim_platform.desktop.reset import reset_storage
from sim_platform import Platform


class ResetStorageTests(unittest.TestCase):
    def test_failure_after_database_replacement_restores_original_data(self):
        with TemporaryDirectory() as temporary, PlatformLifecycle(Path(temporary)) as lifecycle:
            directory = Path(temporary)
            original = lifecycle.open()
            profile = original.create_profile('Existing researcher')
            account = original.create_account(profile, 'Existing account', 100_000_000)
            settings = directory / 'desktop-settings.json'
            settings.write_text('{"dataset_folder": "/existing/data"}')
            open_research = Platform.open_research

            def fail_reopening(platform, **options):
                if platform.directory == directory:
                    raise OSError('simulated reopen failure')
                return open_research(platform, **options)

            with patch.object(Platform, 'open_research', fail_reopening):
                with self.assertRaisesRegex(OSError, 'simulated'):
                    reset_storage(lifecycle)
            recovered = lifecycle.open()
            self.assertTrue(original.closed)
            self.assertEqual(recovered.accounts(profile)[0]['id'], account)
            self.assertEqual(settings.read_text(), '{"dataset_folder": "/existing/data"}')

    def test_failure_preparing_replacement_keeps_original_database(self):
        with TemporaryDirectory() as temporary, PlatformLifecycle(Path(temporary)) as lifecycle:
            original = lifecycle.open()
            profile = original.create_profile('Existing researcher')
            with patch(
                'sim_platform.research.controller.seed_research',
                side_effect=RuntimeError('cannot prepare reset'),
            ):
                with self.assertRaisesRegex(RuntimeError, 'cannot prepare reset'):
                    reset_storage(lifecycle)
            self.assertTrue(original.closed)
            self.assertEqual(lifecycle.open().profiles()[0]['id'], profile)


if __name__ == '__main__':
    unittest.main()
