"""Every example notebook runs from top to bottom (code cells executed in order)."""

import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import unittest

NOTEBOOKS = Path(__file__).resolve().parents[2] / 'examples' / 'notebooks'


def run_notebook(path):
    """Execute a notebook's code cells in one namespace; return its printed output."""
    namespace = dict(__name__='__notebook__')
    output = io.StringIO()
    cells = json.loads(path.read_text())['cells']
    try:
        with contextlib.redirect_stdout(output):
            for cell in cells:
                if cell['cell_type'] == 'code':
                    exec(compile(''.join(cell['source']), str(path), 'exec'), namespace)
    finally:
        if 'app' in namespace:
            namespace['app'].close()
        if 'folder' in namespace:
            shutil.rmtree(namespace['folder'], ignore_errors=True)
    return output.getvalue()


@unittest.skipUnless(
    os.environ.get('MARKET_LAB_NOTEBOOK_TESTS') == '1', 'notebook suite (about six minutes)'
)
class NotebookTests(unittest.TestCase):
    def test_every_notebook_runs(self):
        paths = sorted(NOTEBOOKS.glob('*.ipynb'))
        self.assertGreaterEqual(len(paths), 3)
        for path in paths:
            with self.subTest(notebook=path.name):
                text = run_notebook(path)
                self.assertTrue(text.strip(), 'the notebook printed nothing')


if __name__ == '__main__':
    unittest.main()
