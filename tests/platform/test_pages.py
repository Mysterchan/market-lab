"""Portable documentation routing and bounded public-site integrity checks."""

from pathlib import Path
import json
from tempfile import TemporaryDirectory
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
from build_pages import check_links, relative, tutorial_chapters
from build_source_release import public_path


class PagesTests(unittest.TestCase):
    def test_recorded_tutorial_metadata_builds_current_homepage_chapters(self):
        evidence = json.loads((ROOT / 'docs/site/media/demo-evidence.json').read_text())
        buttons = tutorial_chapters(evidence)
        self.assertIn('Trading Workspace</button>', buttons)
        self.assertIn('Verified Replay</button>', buttons)
        self.assertEqual(buttons.count('data-seek='), len(evidence['captions']))

    def test_tutorial_titles_can_change_without_hardcoded_chapter_names(self):
        buttons = tutorial_chapters(
            {'captions': [{'chapter': '09 / Inspect <orders>', 'start': 72.5}]}
        )
        self.assertIn('data-seek="72"', buttons)
        self.assertIn('<time>1:12</time>Inspect &lt;Orders&gt;', buttons)
        with self.assertRaisesRegex(ValueError, 'missing a chapter title'):
            tutorial_chapters({'captions': [{'start': 0, 'text': 'Caption'}]})

    def test_nested_pages_use_relative_urls_for_either_project_prefix(self):
        self.assertEqual(
            relative('media/demo.mp4', 'docs/user-guide/demo.html'), '../../media/demo.mp4'
        )
        self.assertEqual(
            relative('docs/user-guide/README.html', 'index.html'), 'docs/user-guide/README.html'
        )
        self.assertEqual(
            relative('index.html#watch', '_source/src/book/book.cpp.html'),
            '../../../index.html#watch',
        )

    def test_site_integrity_checks_assets_and_anchors(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'index.html').write_text(
                '<a href="guide.html#replay">Replay</a><video poster="poster.jpg"><source src="demo.mp4"></video>'
            )
            (root / 'guide.html').write_text(
                '<h2 id="replay">Replay</h2><a href="index.html">Home</a>'
            )
            (root / 'poster.jpg').write_bytes(b'asset')
            (root / 'demo.mp4').write_bytes(b'asset')
            self.assertEqual(check_links(root), 2)
            (root / 'poster.jpg').unlink()
            with self.assertRaisesRegex(ValueError, 'missing poster.jpg'):
                check_links(root)

    def test_missing_anchor_is_rejected(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'index.html').write_text('<a href="#missing">Missing</a>')
            with self.assertRaisesRegex(ValueError, 'missing anchor'):
                check_links(root)

    def test_links_cannot_escape_site_root(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'index.html').write_text('<a href="../private.txt">Private</a>')
            with self.assertRaisesRegex(ValueError, 'URL leaves site'):
                check_links(root)

    def test_source_exports_include_site_dependencies_and_media(self):
        for path in (
            'requirements-site.txt',
            'requirements-demo.txt',
            'docs/site/media/demo.mp4',
            '.github/workflows/pages.yml',
        ):
            with self.subTest(path=path):
                self.assertTrue(public_path(path))
        self.assertFalse(public_path('internal/reviews/private.md'))
        self.assertFalse(public_path('docs/.env'))
