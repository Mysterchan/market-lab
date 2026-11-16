"""Worker data reaches presentation on Qt's application thread."""

import os
import unittest
from support.desktop import DesktopFixture


@unittest.skipUnless(os.environ.get('TRADING_DESKTOP_TESTS') == '1', 'optional Desktop suite')
class DatasetThreadTests(DesktopFixture, unittest.TestCase):
    def test_worker_catalog_is_delivered_on_the_gui_thread(self):
        from PySide6.QtCore import QThread

        observed = []

        def received(kind, value):
            if kind == 'datasets':
                observed.append((QThread.currentThread(), value))

        self.window.manager.event.connect(received)
        self.window.dispatch('refresh', {})
        self.until(lambda: bool(observed))
        self.assertIs(observed[0][0], self.qt.thread())
        self.assertEqual(observed[0][1]['files'][0]['name'], self.source[0].name)
