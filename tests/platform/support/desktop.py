"""Real HTML Desktop fixture with temporary Platform data and owned Qt cleanup."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import time


class DesktopFixture:
    @classmethod
    def setUpClass(cls):
        from sim_platform.desktop.app import create_application

        cls.qt = create_application()

    def setUp(self):
        from sim_platform.desktop.app import open_window
        from sim_platform.examples.market_demo import write_fixture

        self.temp = TemporaryDirectory(prefix='web-desktop-')
        self.path = Path(self.temp.name)
        self.source = write_fixture(self.path / 'fixture')
        self.window = open_window(self.path / 'accounts', demo_source=self.source)
        self.errors = []
        self.window.browser.commands.event.connect(self.capture)
        self.window.browser.page().failed.connect(self.errors.append)
        self.window.show()
        self.addCleanup(self.shutdown)
        self.until(lambda: self.window.catalogue.get('accounts'))
        self.until(lambda: self.js("document.querySelectorAll('.nav').length") > 0)

    def capture(self, encoded):
        event = json.loads(encoded)
        if event['kind'] == 'error':
            self.errors.append(event['value']['message'])

    def until(self, predicate, timeout=20):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            self.qt.processEvents()
            if predicate():
                return
            time.sleep(0.005)
        self.fail('GUI condition timed out: ' + repr(self.errors))

    def js(self, source):
        values = []
        self.window.browser.page().runJavaScript(source, values.append)
        self.until(lambda: bool(values))
        return values[0]

    def click(self, selector):
        self.js('document.querySelector(' + json.dumps(selector) + ').click()')

    def shutdown(self):
        from PySide6.QtCore import QCoreApplication, QEvent
        from shiboken6 import isValid

        from unittest.mock import patch

        with patch.object(self.window, 'confirm_close', return_value=True):
            self.window.close()
        self.until(
            lambda: all(not owner.thread.isRunning() for owner in self.window.owners.values())
        )
        self.qt.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.until(lambda: not isValid(self.window))
        self.temp.cleanup()

    def prepare(self, name, cash, execution_mode="controlled"):
        before = set(self.window.views)
        self.window.dispatch(
            'prepare',
            dict(
                path=str(self.source[0]),
                symbol='AAPL',
                account_name=name,
                cash=cash,
                policy='human',
                execution_mode=execution_mode,
                fee_per_share='.01',
                latency_ns=0,
                quantity=10,
                hold_ns=2_000_000_000,
                start_mode='first_book',
            ),
        )
        self.until(
            lambda: any(
                key not in before and value.get('run') for key, value in self.window.views.items()
            )
        )
        return next(key for key in self.window.views if key not in before)
