"""Windows project traversal preserves import limits without POSIX-only flags."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from sim_platform.domain.models import ContractError
from sim_platform.persistence.agents.project_capture_windows import capture_project


class ProjectCaptureTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / 'agent.py').write_bytes(b'def create_agent(context): return None\n')

    def test_folder_and_single_file_capture_without_descriptor_traversal(self):
        (self.root / '.env').write_text('private')
        (self.root / '.venv').mkdir()
        (self.root / 'tools').mkdir()
        (self.root / 'tools/math.py').write_bytes(b'value = 2\n')
        files, excluded = capture_project(self.root)
        self.assertEqual(set(files), {'agent.py', 'tools/math.py'})
        self.assertEqual(files['tools/math.py'], 'value = 2\n')
        self.assertEqual(excluded, ['.env', '.venv'])
        self.assertEqual(
            capture_project(self.root / 'agent.py'), ({'agent.py': files['agent.py']}, [])
        )

    def test_capture_preserves_utf8_and_original_line_endings(self):
        from sim_platform.persistence.agents.projects import capture_project as capture_native

        for content in (
            "label = '價格'\nvalue = 2\n".encode('utf-8'),
            "label = '價格'\r\nvalue = 2\r\n".encode('utf-8'),
            "label = '價格'\r\nvalue = 2\n".encode('utf-8'),
        ):
            (self.root / 'agent.py').write_bytes(content)
            for capture in (capture_project, capture_native):
                with self.subTest(content=content, capture=capture.__module__):
                    folder, _ = capture(self.root)
                    single, _ = capture(self.root / 'agent.py')
                    self.assertEqual(folder['agent.py'].encode('utf-8'), content)
                    self.assertEqual(single['agent.py'].encode('utf-8'), content)

    def test_unsupported_and_oversize_files_are_rejected(self):
        path = self.root / 'data.exe'
        path.write_bytes(b'unsupported')
        with self.assertRaisesRegex(ContractError, 'unsupported'):
            capture_project(self.root)
        path.unlink()
        (self.root / 'large.txt').write_bytes(b'x' * (5 * 1024 * 1024 + 1))
        with self.assertRaisesRegex(ContractError, '5 MiB'):
            capture_project(self.root)

    def test_saved_and_execution_copies_preserve_source_bytes(self):
        from sim_platform.persistence.agents.projects import ProjectFiles
        from sim_platform.persistence.workspace.agent_source import AgentSourceFiles

        store = ProjectFiles(self.root / 'app')
        for content in (b'value = 2\n', b'value = 2\r\n', b'value = 2\r\nother = 3\n'):
            with self.subTest(content=content):
                files = {'agent.py': content.decode('utf-8')}
                report = store.save(
                    files,
                    {
                        'profile_id': 'owner',
                        'files': [{'path': 'agent.py', 'bytes': len(content)}],
                    },
                )
                saved, _ = store.read(report['project_ref'], 'owner')
                self.assertEqual(saved, files)
                self.assertEqual(
                    (store.directory(report['project_ref']) / 'source/agent.py').read_bytes(),
                    content,
                )
                execution = self.root / 'execution'
                execution.mkdir()
                source = AgentSourceFiles(execution, saved)
                try:
                    self.assertEqual((execution / 'agent.py').read_bytes(), content)
                finally:
                    source.close()

    def test_reparse_attributes_and_replaced_source_are_rejected(self):
        from sim_platform.persistence.agents import project_capture_windows as capture
        from types import SimpleNamespace

        with patch.object(
            Path, 'lstat', return_value=SimpleNamespace(st_mode=0o100644, st_file_attributes=0x400)
        ):
            with self.assertRaisesRegex(ContractError, 'reparse'):
                capture_project(self.root / 'agent.py')
        original = capture.read_text

        def replace_after_read(descriptor):
            result = original(descriptor)
            path = self.root / 'agent.py'
            path.write_text('changed\n')
            return result

        with patch.object(capture, 'read_text', replace_after_read):
            with self.assertRaisesRegex(ContractError, 'changed'):
                capture_project(self.root / 'agent.py')
