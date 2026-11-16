"""Unattended tools drive the sole Desktop window against explicit temporary data."""

import json
import time


class DesktopDriver:
    def __init__(self, directory, source, *, timeout=30, on_poll=None):
        from sim_platform.desktop.app import create_application, open_window

        self.qt = create_application()
        self.window = open_window(directory, demo_source=source)
        self.window.confirm_close = lambda: True
        self.window.show()
        self.timeout, self.on_poll = timeout, on_poll
        self.errors = []
        self.identity = 'manager'
        self.window.browser.page().failed.connect(self.errors.append)
        self.window.browser.commands.event.connect(self.capture)

    def capture(self, encoded):
        event = json.loads(encoded)
        if event['kind'] == 'error':
            self.errors.append(event['value']['message'])

    @property
    def view(self):
        return self.window.views.get(self.identity, {})

    def until(self, predicate, *, check_errors=True):
        limit = time.monotonic() + self.timeout
        while True:
            self.qt.processEvents()
            if self.on_poll:
                self.on_poll()
            if check_errors and self.errors:
                raise RuntimeError('; '.join(self.errors))
            if predicate():
                return
            if time.monotonic() > limit:
                raise TimeoutError('Desktop operation exceeded deadline')
            time.sleep(0.002)

    def js(self, source):
        result = []
        self.window.browser.page().runJavaScript(source, result.append)
        self.until(lambda: bool(result))
        return result[0]

    def click(self, selector):
        self.js('document.querySelector(' + json.dumps(selector) + ').click()')

    def prepare(self, path, *, policy='human', start_ns=None, symbol='AAPL'):
        self.until(lambda: bool(self.window.catalogue.get('accounts')))
        before = set(self.window.views)
        options = dict(
            path=str(path),
            symbol=symbol,
            account_name='Control check',
            cash='10000',
            policy=policy,
            execution_mode='controlled',
            quantity=10,
            hold_ns=2_000_000_000,
            fee_per_share='.01',
            latency_ns=0,
            start_mode='custom' if start_ns is not None else 'first_book',
        )
        if start_ns is not None:
            options['start_time_ns'] = start_ns
        self.window.dispatch('prepare', options)
        self.until(
            lambda: any(k not in before and v.get('run') for k, v in self.window.views.items())
        )
        self.identity = next(
            k for k, v in self.window.views.items() if k not in before and v.get('run')
        )
        return self.identity

    def operation(self, method, **arguments):
        self.window.dispatch('session', dict(session=self.identity, method=method, **arguments))

    def close(self):
        from PySide6.QtCore import QCoreApplication, QEvent
        from shiboken6 import isValid

        self.window.close()
        self.until(
            lambda: all(not owner.thread.isRunning() for owner in self.window.owners.values()),
            check_errors=False,
        )
        self.qt.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        if isValid(self.window):
            raise RuntimeError('Desktop window did not release its resources')
