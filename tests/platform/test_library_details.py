"""What the Desktop library says about each recording: file type and bar-file dates."""

from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from sim_platform.desktop.presentation import describe_datasets
from sim_platform.market.prepared.bars import bar_file_span

HEADER = 'date,open,high,low,close,volume\n'


class LibraryDetailsTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory(prefix='library-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def write(self, name, text):
        path = self.root / name
        path.write_text(text)
        stat = path.stat()
        return dict(
            path=str(path), status='Available', bytes=stat.st_size, mtime_ns=stat.st_mtime_ns
        )

    def test_bar_files_give_their_date_range_in_either_row_order(self):
        rows = ['2026-01-05,1,1,1,1,1\n', '2026-01-06,1,1,1,1,1\n', '2026-02-10,1,1,1,1,1\n']
        oldest = self.write('a.csv', HEADER + ''.join(rows))
        newest = self.write('b.csv', HEADER + ''.join(reversed(rows)) + '\n')
        self.assertEqual(bar_file_span(Path(oldest['path'])), ('2026-01-05', '2026-02-10'))
        files = describe_datasets(dict(files=[oldest, newest]))['files']
        for entry in files:
            self.assertEqual(entry['kind'], 'Price bars · CSV')
            self.assertEqual(entry['dates'], '2026-01-05 to 2026-02-10')

    def test_itch_and_unreadable_files_get_a_kind_but_no_dates(self):
        itch = self.write('feed.bin', 'x')
        broken = self.write('broken.csv', 'not,a,bar,file\n')
        missing = dict(self.write('gone.csv', HEADER), status='Missing')
        files = describe_datasets(dict(files=[itch, broken, missing]))['files']
        self.assertEqual([f['kind'] for f in files][0], 'ITCH 5.0 messages')
        self.assertTrue(all('dates' not in f for f in files))


if __name__ == '__main__':
    unittest.main()
