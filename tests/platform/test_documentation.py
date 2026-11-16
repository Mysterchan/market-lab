"""Maintained documentation checks work in exported source without Git or network."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tools.check_documentation import check


class DocumentationTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')

    def test_heading_links_and_examples_need_no_network_or_git(self):
        self.write('README.md', '[Guide](docs/guide.md#first-run)\n[Site](https://example.org)\n')
        self.write('docs/guide.md', '# First run\n```md\n[Example](not-a-real-file)\n```\n')
        pages, errors = check(self.root)
        self.assertEqual(len(pages), 2)
        self.assertEqual(errors, [])

    def test_missing_target_and_heading_are_reported(self):
        self.write('README.md', '[Guide](docs/guide.md#unknown)\n![Image](missing.png)\n')
        self.write('docs/guide.md', '# First run\n')
        _, errors = check(self.root)
        self.assertEqual(len(errors), 2)
        self.assertTrue(any('missing heading' in error for error in errors))
        self.assertTrue(any('missing local target' in error for error in errors))

    def test_current_text_and_api_docstrings_use_english_but_archives_are_preserved(self):
        chinese = chr(0x4E2D)
        self.write('README.md', '# Guide ' + chinese)
        self.write('sim_platform/module.py', '"""' + chinese + '"""\n')
        self.write('internal/README.md', '# Development ' + chinese)
        self.write('internal/archive/old.md', '# Original ' + chinese)
        pages, errors = check(self.root, include_internal=True)
        self.assertEqual(len(pages), 2)
        self.assertEqual(len(errors), 3)

    def test_public_pages_cannot_require_internal_or_external_local_files(self):
        self.write('README.md', '[Private](internal/plan.md)\n[Outside](../outside.md)\n')
        self.write('internal/plan.md', '# Plan\n')
        _, errors = check(self.root)
        self.assertTrue(any('depends on internal' in error for error in errors))
        self.assertTrue(any('leaves repository' in error for error in errors))

    def test_cantonese_user_guides_keep_language_and_still_check_links(self):
        chinese = chr(0x4E2D)
        self.write('docs/user-guide/desktop-zh-hk.md', '# ' + chinese + '\n[Missing](missing.md)\n')
        self.write('docs/user-guide/desktop.md', '# ' + chinese)
        self.write('docs/reference/api-zh-hk.md', '# ' + chinese)
        self.write('sim_platform/module.py', '"""' + chinese + '"""\n')
        _, errors = check(self.root)
        self.assertEqual(len(errors), 4)
        self.assertTrue(any('desktop-zh-hk.md:2: missing local target' in e for e in errors))
        self.assertFalse(any('desktop-zh-hk.md:1:' in e for e in errors))
        self.assertTrue(any('docs/user-guide/desktop.md:1: write current' in e for e in errors))
        self.assertTrue(any('docs/reference/api-zh-hk.md:1: write current' in e for e in errors))
        self.assertTrue(any('write module and API documentation in English' in e for e in errors))

    def test_dependency_and_generated_markdown_are_not_maintained_guides(self):
        self.write('docs/site/README.md', '# Site\n')
        for directory in ('node_modules/vendor', 'build', '.venv', '__pycache__'):
            self.write('docs/site/' + directory + '/README.md', '[Missing](not-in-project.md)')
        pages, errors = check(self.root)
        self.assertEqual(pages, [(self.root / 'docs/site/README.md').resolve()])
        self.assertEqual(errors, [])
