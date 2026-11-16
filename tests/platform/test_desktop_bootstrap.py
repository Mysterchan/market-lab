"""Isolated real-WebEngine startup check for the configured Desktop test backend."""

import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
ENABLED = os.environ.get('TRADING_DESKTOP_TESTS') == '1'


@unittest.skipUnless(ENABLED, 'optional desktop suite; set TRADING_DESKTOP_TESTS=1')
class DesktopBootstrapTests(unittest.TestCase):
    def test_late_webengine_import_can_render_with_shared_contexts(self):
        # A fresh process exposes the startup-order bug even if another suite has
        # already imported WebEngine or created QApplication in the parent.
        program = '''
import sys
from sim_platform.desktop.app import create_application
assert not any(name.startswith('PySide6') for name in sys.modules)
application = create_application()
from PySide6.QtCore import QEventLoop, QTimer, Qt
assert application.testAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
assert create_application() is application
assert 'PySide6.QtWebEngineWidgets' not in sys.modules
from PySide6.QtWebEngineWidgets import QWebEngineView

view = QWebEngineView()
view.resize(320, 200)
loop = QEventLoop()
results = []

def received(value):
    results.append(value)
    loop.quit()

def loaded(success):
    if success:
        view.page().runJavaScript('document.querySelector("h1").textContent', received)
    else:
        loop.quit()

view.loadFinished.connect(loaded)
view.setHtml('<html><body><h1>WebEngine ready</h1></body></html>')
view.show()
QTimer.singleShot(10000, loop.quit)
loop.exec()
assert results == ['WebEngine ready'], results
assert not view.grab().isNull()
view.close()
print('Shared contexts and real WebEngine rendering verified')
'''
        result = subprocess.run(
            [sys.executable, '-X', 'faulthandler', '-c', program],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(
            result.returncode,
            0,
            f'WebEngine startup failed:\n{result.stdout}\n{result.stderr}',
        )
        self.assertIn('real WebEngine rendering verified', result.stdout)
        self.assertNotIn('must be set before QCoreApplication is created', result.stderr)
