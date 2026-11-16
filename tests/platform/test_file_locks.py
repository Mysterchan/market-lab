"""Cross-process ownership and workspace cleanup without a native market build."""

import errno
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from sim_platform.domain.models import ContractError
from sim_platform.persistence import locking
from sim_platform.persistence.workspace.temporary import AppWorkspace
from sim_platform.persistence.workspace.ownership import AccountLease
from sim_platform.persistence.prepared.store import ImportLease

ROOT = Path(__file__).resolve().parents[2]
PROBE = '''from pathlib import Path
import sys
from sim_platform.persistence.locking import acquire_exclusive
with Path(sys.argv[1]).open('ab') as stream:
    print(acquire_exclusive(stream, blocking=False))
'''
HOLDER = '''from pathlib import Path
import sys, time
from sim_platform.persistence.locking import acquire_exclusive
with Path(sys.argv[1]).open('ab') as stream:
    acquire_exclusive(stream)
    Path(sys.argv[2]).touch()
    time.sleep(30)
'''


class FileLockTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.path = self.root / 'owner.lock'

    def probe(self):
        result = subprocess.run(
            [sys.executable, '-c', PROBE, str(self.path)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        return result.stdout.strip() == 'True'

    def test_exclusive_owner_blocks_other_process_until_released(self):
        with self.path.open('ab') as owner:
            self.assertTrue(locking.acquire_exclusive(owner))
            self.assertFalse(self.probe())
            locking.release_exclusive(owner)
            self.assertTrue(self.probe())

    def test_process_exit_releases_lock_without_explicit_unlock(self):
        ready = self.root / 'ready'
        process = subprocess.Popen(
            [sys.executable, '-c', HOLDER, str(self.path), str(ready)],
            cwd=ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        try:
            deadline = time.monotonic() + 5
            while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(ready.exists(), 'child did not acquire lock')
            self.assertFalse(self.probe())
        finally:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=5)
            process.stderr.close()
        self.assertTrue(self.probe())

    def test_live_workspace_survives_cleanup_and_closed_workspace_is_removed(self):
        cache = self.root / 'cache'
        first = AppWorkspace(cache)
        self.addCleanup(first.close)
        stale = cache / 'workspace-stale'
        stale.mkdir()
        (stale / 'owner.lock').touch()
        second = AppWorkspace(cache)
        self.addCleanup(second.close)
        self.assertTrue(first.path.exists())
        self.assertFalse(stale.exists())
        second.close()
        self.assertFalse(second.path.exists())
        self.assertTrue(first.path.exists())
        first.close()
        self.assertFalse(first.path.exists())

    def test_account_and_import_leases_remain_exclusive(self):
        for factory in (lambda: AccountLease(self.root, 'account'), lambda: ImportLease(self.root)):
            owner = factory()
            try:
                with self.assertRaises(ContractError):
                    factory()
            finally:
                owner.close()
            replacement = factory()
            replacement.close()
            replacement.close()

    def test_windows_byte_offset_and_contention_errors(self):
        crt = Mock(LK_NBLCK=2, LK_UNLCK=0)
        stream = Mock()
        stream.fileno.return_value = 17
        with patch.dict(sys.modules, {'msvcrt': crt}):
            self.assertTrue(locking._try_windows_lock(stream))
            stream.seek.assert_called_once_with(0)
            crt.locking.assert_called_once_with(17, 2, 1)
            crt.locking.side_effect = OSError(errno.EACCES, 'held by another owner')
            self.assertFalse(locking._try_windows_lock(stream))
            crt.locking.side_effect = OSError(errno.EIO, 'disk failure')
            with self.assertRaises(OSError):
                locking._try_windows_lock(stream)
            crt.locking.side_effect = None
            stream.seek.reset_mock()
            with patch.object(locking, 'os', SimpleNamespace(name='nt')):
                locking.release_exclusive(stream)
            stream.seek.assert_called_once_with(0)
            crt.locking.assert_called_with(17, 0, 1)
