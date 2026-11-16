"""Runtime selection, cancellation and hostile archive boundaries without network access."""

import io
import json
from pathlib import Path
import sys
import tarfile
from threading import Event
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch
from sim_platform import Platform
from sim_platform.agents.preparation.errors import PreparationError, PreparationCancelled
from sim_platform.agents.runtimes.manager import PythonRuntimes, RuntimePreparation
from sim_platform.agents.runtimes.versions import PythonRequest
from sim_platform.agents.runtimes.provider import UvPythonProvider
from sim_platform.persistence.agents.runtimes import RuntimeFiles
from sim_platform.domain.models import ContractError


class PythonRuntimeTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.files = RuntimeFiles(self.root)
        self.provider = Mock()
        self.manager = PythonRuntimes(self.files, self.provider)

    def test_minor_patch_and_range_have_distinct_meaning(self):
        self.assertTrue(PythonRequest('3.12').matches('3.12.13'))
        self.assertFalse(PythonRequest('3.12.1').matches('3.12.13'))
        request = PythonRequest('>=3.12.1,<3.12.2')
        self.assertEqual(request.candidates, [(3, 12)])
        self.assertTrue(request.matches('3.12.1'))
        self.assertFalse(request.matches('3.12.2'))
        for value in ('3.8', '>=3.15', '>=3.13,<3.12', 'newest', '3.12; echo x'):
            with self.subTest(value=value), self.assertRaises(PreparationError):
                PythonRequest(value)

    def test_new_project_default_does_not_inherit_app_python(self):
        source = self.root / 'agent.py'
        source.write_text('def create_agent(context): return None\n')
        with Platform(self.root / 'app') as app:
            self.assertEqual(app.check_agent_project(source)['python'], '3.12')

    def test_missing_runtime_requires_explicit_download_permission(self):
        with patch('sim_platform.agents.runtimes.manager.app_python', return_value=None):
            with self.assertRaises(PreparationError) as error:
                self.manager.prepare('3.12', lambda: False, lambda phase: None)
        self.assertEqual(error.exception.code, 'PYTHON_UNAVAILABLE')
        self.provider.download.assert_not_called()

    def test_borrowed_app_must_match_and_frozen_app_is_not_a_runtime(self):
        current = f'{sys.version_info.major}.{sys.version_info.minor}'
        self.assertEqual(self.manager.ready(current)['origin'], 'app')
        with patch.object(sys, 'frozen', True, create=True):
            self.assertIsNone(self.manager.ready(current))

    def test_missing_manager_is_clear_failure(self):
        with self.assertRaises(PreparationError) as error:
            UvPythonProvider(self.root / 'missing').download(self.root, '3.12', lambda: False)
        self.assertEqual(error.exception.code, 'PYTHON_MANAGER_UNAVAILABLE')

    def test_narrow_range_reaches_provider_and_failed_candidate_is_removed(self):
        self.provider.download.side_effect = PreparationError('PYTHON_DOWNLOAD_FAILED', 'offline')
        with patch('sim_platform.agents.runtimes.manager.app_python', return_value=None):
            with self.assertRaises(PreparationError):
                self.manager.prepare(
                    '>=3.12.1,<3.12.2', lambda: False, lambda phase: None, allow_download=True
                )
        self.assertEqual(self.provider.download.call_args.args[1], '>=3.12.1,<3.12.2,>=3.9,<3.15')
        self.assertEqual(self.files.records(), [])
        self.assertEqual([path for path in self.files.root.iterdir() if path.is_dir()], [])

    def test_cancelled_download_cannot_publish_ready(self):
        self.provider.download.side_effect = PreparationCancelled()
        with patch('sim_platform.agents.runtimes.manager.app_python', return_value=None):
            job = RuntimePreparation(self.manager, '3.12', allow_download=True)
            try:
                self.assertEqual(job.wait(5)['state'], 'cancelled')
            finally:
                job.close()
        self.assertEqual(self.files.records(), [])
        self.assertEqual([path for path in self.files.root.iterdir() if path.is_dir()], [])

    def test_managed_runtime_reused_without_downloading(self):
        candidate = self.files.allocate()
        executable = candidate / 'python'
        executable.touch()
        record = self.files.publish(
            candidate, dict(version='3.12.1', executable=str(executable)), 'offline'
        )
        result, reused = self.manager.prepare('3.12', lambda: False, lambda phase: None)
        self.assertTrue(reused)
        self.assertEqual(result, record)
        self.provider.download.assert_not_called()

    def test_two_jobs_publish_one_runtime_and_waiter_can_cancel(self):
        entered, release = Event(), Event()

        def download(target, request, cancelled):
            binary = target / 'installation' / 'python' / 'bin' / 'python3'
            binary.parent.mkdir(parents=True)
            binary.touch()
            entered.set()
            while not release.wait(0.01):
                if cancelled():
                    raise PreparationCancelled()

        def probe(executable, cancelled):
            return dict(
                version='3.12.13',
                executable=str(executable),
                base_prefix=str(executable.parent.parent),
            )

        self.provider.download.side_effect = download
        with (
            patch('sim_platform.agents.runtimes.manager.app_python', return_value=None),
            patch('sim_platform.agents.runtimes.manager.inspect_python', side_effect=probe),
        ):
            first = RuntimePreparation(self.manager, '3.12', allow_download=True)
            self.assertTrue(entered.wait(5))
            waiter = RuntimePreparation(self.manager, '3.12', allow_download=True)
            try:
                waiter.cancel()
                self.assertEqual(waiter.wait(5)['state'], 'cancelled')
                second = RuntimePreparation(self.manager, '3.12', allow_download=True)
                try:
                    release.set()
                    self.assertEqual(first.wait(5)['state'], 'ready')
                    status = second.wait(5)
                    self.assertEqual(status['state'], 'ready', status)
                    self.assertTrue(status['reused'])
                finally:
                    second.close()
            finally:
                release.set()
                first.close()
                waiter.close()
        self.assertEqual(self.provider.download.call_count, 1)
        self.assertEqual(len(self.files.records()), 1)

    def archive(self, name, *, link=None):
        archive = self.root / 'runtime.tar.gz'
        with tarfile.open(archive, 'w:gz') as bundle:
            entry = tarfile.TarInfo(name)
            if link is not None:
                entry.type = tarfile.SYMTYPE
                entry.linkname = link
                bundle.addfile(entry)
            else:
                entry.size = 1
                bundle.addfile(entry, io.BytesIO(b'x'))
        return archive

    def test_archive_cannot_write_outside_private_candidate(self):
        for name, link in (
            ('../outside', None),
            ('/outside', None),
            ('python/bin/link', '../../../outside'),
        ):
            with self.subTest(name=name, link=link), self.assertRaises(ContractError):
                self.files.unpack(self.archive(name, link=link), self.root / 'target', lambda: None)
        self.assertFalse((self.root / 'outside').exists())

    def test_invalid_archive_job_cleans_candidate_and_reports_failure(self):
        job = RuntimePreparation(self.manager, '3.12', archive=self.archive('../outside'))
        try:
            self.assertEqual(job.wait(5)['state'], 'failed')
        finally:
            job.close()
        self.assertEqual(self.files.records(), [])
        self.assertEqual([path for path in self.files.root.iterdir() if path.is_dir()], [])

    def test_incomplete_metadata_not_listed(self):
        candidate = self.files.allocate()
        (candidate / 'runtime.json').write_text(json.dumps({'runtime_ref': candidate.name}))
        self.assertEqual(self.files.records(), [])


if __name__ == '__main__':
    unittest.main()
