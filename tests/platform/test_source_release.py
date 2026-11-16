"""Public export provenance, reproducibility and safe incremental synchronization."""

import contextlib
import io
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tarfile
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
from build_source_release import build, PROVENANCE
from sync_public_source import sync, MANIFEST, read_candidate


@unittest.skipUnless(shutil.which('make'), 'Make is required for source build checks')
class MakeInvocationTests(unittest.TestCase):
    def test_desktop_respects_the_explicit_display_backend(self):
        result = subprocess.run(
            ['make', '-n', 'test-desktop', 'QT_QPA_PLATFORM=xcb'],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertIn('QT_QPA_PLATFORM=xcb', ' '.join(shlex.split(result.stdout)))
        self.assertNotIn('QT_QPA_PLATFORM=offscreen', result.stdout)

    def test_selected_python_path_with_spaces_is_one_executable(self):
        executable = '/tmp/python installation with spaces/bin/python'
        for target in ('test', 'test-desktop', 'python-quality'):
            with self.subTest(target=target):
                result = subprocess.run(
                    ['make', '-n', target, 'PYTHON=' + executable],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    check=True,
                )
                commands = [line for line in result.stdout.splitlines() if executable in line]
                self.assertTrue(commands, result.stdout)
                for command in commands:
                    self.assertIn(executable, shlex.split(command))


class SourceReleaseTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.source = self.root / 'internal'
        self.source.mkdir()
        self.destination = self.root / 'public'
        self.output = self.root / 'archives'
        self.git(self.source, 'init', '-q')
        for name, content in {
            'README.md': 'Public guide\n',
            'LICENSE': 'License\n',
            'NOTICE': 'Notice\n',
            'sim_platform/__init__.py': "__version__ = '0.1.0'\n",
            'sim_platform/desktop/web/frontend/index.html': '<html></html>',
            'docs/old.md': 'old',
            'internal/plan.md': 'private plan',
            'docs/.env': 'secret',
        }.items():
            self.write(name, content)
        self.commit(self.source)
        self.quiet = contextlib.redirect_stdout(io.StringIO())
        self.quiet.__enter__()
        self.addCleanup(self.quiet.__exit__, None, None, None)

    def git(self, root, *args):
        return subprocess.run(
            ['git', '-c', 'core.autocrlf=false', '-C', str(root), *args],
            check=True,
            capture_output=True,
        ).stdout

    def commit(self, root):
        self.git(root, 'add', '.')
        self.git(
            root,
            '-c',
            'user.name=Fixture',
            '-c',
            'user.email=fixture@example.invalid',
            'commit',
            '-qm',
            'Fixture',
        )

    def write(self, name, content):
        path = self.source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        # Bytes, not text: Windows would turn '\n' into '\r\n' and change the digests.
        path.write_bytes(content.encode() if isinstance(content, str) else content)

    def archive(self, **kwargs):
        return build(self.output, root=self.source, **kwargs)

    def test_commit_export_ignores_dirty_and_untracked_files_and_is_reproducible(self):
        first = self.archive().read_bytes()
        self.write('README.md', 'unreviewed')
        self.write('docs/new.md', 'not committed')
        archive = self.archive()
        self.assertEqual(first, archive.read_bytes())
        files, provenance = read_candidate(archive, False)
        self.assertEqual(files['README.md'][0], b'Public guide\n')
        self.assertNotIn('docs/new.md', files)
        self.assertNotIn('internal/plan.md', files)
        self.assertNotIn('docs/.env', files)
        self.assertEqual(provenance['source_kind'], 'commit')
        self.assertEqual(
            provenance['source_commit'], self.git(self.source, 'rev-parse', 'HEAD').decode().strip()
        )

    def test_candidate_requires_explicit_choice_and_preview_does_not_write(self):
        self.write('docs/new.md', 'new')
        archive = self.archive(working_tree=True)
        with self.assertRaisesRegex(ValueError, '--allow-candidate'):
            sync(archive, self.destination)
        plan = sync(archive, self.destination, allow_candidate=True)
        self.assertIn('docs/new.md', plan['added'])
        self.assertFalse(self.destination.exists())

    def test_incremental_add_modify_delete_preserves_unmanaged_files(self):
        sync(self.archive(), self.destination, apply=True)
        self.git(self.destination, 'init', '-q')
        (self.destination / 'PUBLIC_ONLY.md').write_text('keep')
        self.commit(self.destination)
        self.write('README.md', 'updated')
        self.write('docs/new.md', 'new')
        (self.source / 'docs/old.md').unlink()
        self.commit(self.source)
        plan = sync(self.archive(), self.destination, apply=True)
        self.assertEqual(plan['deleted'], ['docs/old.md'])
        self.assertIn('README.md', plan['modified'])
        self.assertEqual((self.destination / 'PUBLIC_ONLY.md').read_text(), 'keep')
        self.assertFalse((self.destination / 'docs/old.md').exists())
        self.assertEqual((self.destination / 'README.md').read_text(), 'updated')
        manifest = json.loads((self.destination / MANIFEST).read_text())
        self.assertIn(PROVENANCE, manifest['files'])

    def test_public_local_changes_block_every_write(self):
        sync(self.archive(), self.destination, apply=True)
        self.git(self.destination, 'init', '-q')
        self.commit(self.destination)
        (self.destination / 'README.md').write_text('public hotfix')
        with self.assertRaisesRegex(ValueError, 'local changes'):
            sync(self.archive(), self.destination, apply=True)
        self.assertEqual((self.destination / 'README.md').read_text(), 'public hotfix')

    def test_nonempty_nonrepository_is_not_adopted(self):
        self.destination.mkdir()
        (self.destination / 'notes.txt').write_text('keep')
        with self.assertRaisesRegex(ValueError, 'clean Git'):
            sync(self.archive(), self.destination, apply=True)
        self.assertEqual(list(self.destination.iterdir()), [self.destination / 'notes.txt'])

    def test_traversal_and_symlink_archive_members_are_rejected(self):
        for name, kind in [
            ('market-lab-x/docs/../../outside', tarfile.REGTYPE),
            ('market-lab-x/docs/link', tarfile.SYMTYPE),
        ]:
            archive = self.root / 'unsafe.tar.gz'
            with tarfile.open(archive, 'w:gz') as output:
                member = tarfile.TarInfo(name)
                member.type = kind
                member.linkname = '/tmp/outside'
                output.addfile(member)
            with self.assertRaisesRegex(ValueError, 'Unexpected archive member'):
                sync(archive, self.destination, apply=True)
            self.assertFalse(self.destination.exists())

    def test_destination_path_collision_does_not_partially_sync(self):
        sync(self.archive(), self.destination, apply=True)
        self.git(self.destination, 'init', '-q')
        (self.destination / 'docs/new.md').write_text('public file')
        self.commit(self.destination)
        self.write('docs/new.md', 'internal file')
        self.commit(self.source)
        with self.assertRaisesRegex(ValueError, 'unmanaged'):
            sync(self.archive(), self.destination, apply=True)
        self.assertEqual((self.destination / 'docs/new.md').read_text(), 'public file')

    def test_write_failure_restores_original_files_and_manifest(self):
        sync(self.archive(), self.destination, apply=True)
        self.git(self.destination, 'init', '-q')
        self.commit(self.destination)
        original = (self.destination / MANIFEST).read_bytes()
        self.write('README.md', 'updated')
        self.write('docs/new.md', 'new')
        self.commit(self.source)
        archive = self.archive()
        write = Path.write_bytes
        failed = False

        def fail_once(path, content):
            nonlocal failed
            if path == self.destination / 'README.md' and not failed:
                failed = True
                raise OSError('disk write failed')
            return write(path, content)

        with patch.object(Path, 'write_bytes', fail_once):
            with self.assertRaisesRegex(OSError, 'disk write failed'):
                sync(archive, self.destination, apply=True)
        self.assertEqual((self.destination / 'README.md').read_text(), 'Public guide\n')
        self.assertEqual((self.destination / MANIFEST).read_bytes(), original)
        self.assertFalse((self.destination / 'docs/new.md').exists())
        self.assertFalse(self.git(self.destination, 'status', '--porcelain'))

    def test_symlink_destination_is_rejected(self):
        target = self.root / 'elsewhere'
        target.mkdir()
        self.destination.symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            sync(self.archive(), self.destination, apply=True)
        self.assertEqual(list(target.iterdir()), [])

    def test_review_copy_update_requires_the_exact_previous_archive(self):
        self.write('docs/retired/topic.md', 'retired guide')
        self.commit(self.source)
        previous = self.root / 'previous.tar.gz'
        previous.write_bytes(self.archive().read_bytes())
        sync(previous, self.destination, apply=True)
        self.write('docs/new.md', 'new')
        (self.source / 'docs/old.md').unlink()
        (self.source / 'docs/retired/topic.md').unlink()
        self.commit(self.source)
        current = self.archive()
        with self.assertRaisesRegex(ValueError, 'manifest'):
            sync(current, self.destination, apply=True, previous_archive=current)
        plan = sync(current, self.destination, apply=True, previous_archive=previous)
        self.assertEqual(plan['deleted'], ['docs/old.md', 'docs/retired/topic.md'])
        self.assertFalse((self.destination / 'docs/retired').exists())
        self.assertEqual((self.destination / 'docs/new.md').read_text(), 'new')

    def test_previous_archive_does_not_overwrite_review_edits_or_added_files(self):
        previous = self.archive()
        sync(previous, self.destination, apply=True)
        (self.destination / 'README.md').write_text('local edit')
        with self.assertRaisesRegex(ValueError, 'local changes'):
            sync(previous, self.destination, apply=True, previous_archive=previous)
        self.assertEqual((self.destination / 'README.md').read_text(), 'local edit')
        (self.destination / 'README.md').write_bytes(b'Public guide\n')
        (self.destination / 'notes.txt').write_text('keep')
        with self.assertRaisesRegex(ValueError, 'added or missing'):
            sync(previous, self.destination, apply=True, previous_archive=previous)

    def test_previous_archive_cannot_bypass_git_cleanliness(self):
        previous = self.archive()
        sync(previous, self.destination, apply=True)
        self.git(self.destination, 'init', '-q')
        self.commit(self.destination)
        (self.destination / 'README.md').write_text('public edit')
        with self.assertRaisesRegex(ValueError, 'clean Git state'):
            sync(previous, self.destination, apply=True, previous_archive=previous)
        self.assertEqual((self.destination / 'README.md').read_text(), 'public edit')
