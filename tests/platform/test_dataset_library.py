"""Dataset identity, reference persistence and import failure safety."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from sim_platform.desktop.datasets import DatasetLibrary
from sim_platform import Platform


class DatasetLibraryTests(unittest.TestCase):
    def dataset_library(self, directory):
        platform = Platform(directory, database=False)
        self.addCleanup(platform.close)
        return DatasetLibrary(platform)

    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / 'downloads' / 'market.bin'
        self.source.parent.mkdir()
        self.source.write_bytes(b'original market data')
        self.directory = self.root / 'app'
        self.library = self.dataset_library(self.directory)

    def test_cancelled_hash_and_copy_leave_no_registration_or_partial_file(self):
        from sim_platform.research.jobs import Cancellation

        self.source.write_bytes(b'x' * (3 * 1024 * 1024))
        for cancel_phase in ('hashing', 'copying'):
            token = Cancellation()

            def progress(value):
                if value['phase'] == cancel_phase:
                    token.cancel()

            with self.subTest(phase=cancel_phase):
                with self.assertRaisesRegex(ValueError, 'cancelled'):
                    self.library.add(self.source, copy=True, cancellation=token, progress=progress)
                self.assertEqual(self.library.catalog()['files'], [])
                copies = self.directory / 'datasets'
                self.assertFalse(copies.exists() and list(copies.iterdir()))
                self.assertTrue(self.source.is_file())

    def test_reference_survives_reopen_missing_file_and_verified_relocation(self):
        item = self.library.add(self.source)['files'][0]
        self.assertEqual(item['path'], str(self.source.resolve()))
        self.assertFalse(item['managed'])
        moved = self.source.with_name('moved.bin')
        self.source.rename(moved)
        reopened = self.dataset_library(self.directory)
        self.assertEqual(reopened.catalog()['files'][0]['status'], 'Missing')
        wrong = moved.with_name('wrong.bin')
        wrong.write_bytes(b'wrong data')
        with self.assertRaisesRegex(ValueError, 'not the same'):
            reopened.locate(item['id'], wrong)
        relocated = reopened.locate(item['id'], moved)['files'][0]
        self.assertEqual(relocated['id'], item['id'])
        self.assertEqual(relocated['status'], 'Available')
        self.assertEqual(
            self.dataset_library(self.directory).catalog()['files'][0]['path'], str(moved.resolve())
        )

    def test_copy_remains_available_without_original_and_storage_change_keeps_old_copy(self):
        item = self.library.add(self.source, copy=True)['files'][0]
        copy = Path(item['path'])
        self.assertNotEqual(copy, self.source)
        self.assertEqual(copy.read_bytes(), self.source.read_bytes())
        self.source.unlink()
        other = self.root / 'other-storage'
        other.mkdir()
        self.library.set_storage(other)
        reopened = self.dataset_library(self.directory)
        self.assertEqual(reopened.storage_root, str(other.resolve()))
        self.assertEqual(reopened.catalog()['files'][0]['status'], 'Available')
        self.assertEqual(reopened.catalog()['files'][0]['path'], str(copy))

    def test_copy_registry_failure_cleans_copy_and_keeps_original(self):
        with patch(
            'sim_platform.persistence.datasets.files._atomic_json',
            side_effect=OSError('cannot save'),
        ):
            with self.assertRaises(OSError):
                self.library.add(self.source, copy=True)
        self.assertEqual(self.source.read_bytes(), b'original market data')
        self.assertEqual(list((self.directory / 'datasets').iterdir()), [])
        self.assertEqual(self.library.entries, [])

    def test_duplicate_reference_and_changed_source(self):
        first = self.library.add(self.source)['files'][0]
        self.assertEqual(len(self.library.add(self.source)['files']), 1)
        self.source.write_bytes(b'changed market data')
        self.assertEqual(self.library.catalog()['files'][0]['status'], 'Changed')
        items = self.library.add(self.source)['files']
        self.assertEqual(len(items), 2)
        self.assertNotEqual(items[-1]['id'], first['id'])

    def test_legacy_folder_is_migrated_once(self):
        self.directory.mkdir()
        (self.directory / 'desktop-settings.json').write_text(
            '{"dataset_folder": ' + __import__('json').dumps(str(self.source.parent)) + '}'
        )
        library = self.dataset_library(self.directory)
        self.assertEqual(len(library.catalog()['files']), 1)
        self.source.with_name('later.bin').write_bytes(b'not yet added')
        self.assertEqual(len(self.dataset_library(self.directory).catalog()['files']), 1)
