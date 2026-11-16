"""The public API matches its record; changing it needs a reviewed record update."""

from pathlib import Path
import json
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))

import check_public_api  # noqa: E402


class PublicApiTests(unittest.TestCase):
    def test_the_public_api_matches_its_record(self):
        recorded = json.loads(check_public_api.RECORD.read_text())
        breaking, added = check_public_api.compare(recorded, check_public_api.current())
        self.assertEqual(breaking, [], 'breaking API change: deprecate first, see api-stability.md')
        self.assertEqual(added, [], 'record additions with tools/check_public_api.py --update')

    def test_a_removed_method_is_breaking(self):
        recorded = {'m': {'A': dict(kind='class', signature='()', members={'f': '(self)'})}}
        now = {'m': {'A': dict(kind='class', signature='()', members={})}}
        self.assertEqual(check_public_api.compare(recorded, now)[0], ['removed m.A.f'])


if __name__ == '__main__':
    unittest.main()
