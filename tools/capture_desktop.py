"""Capture the HTML Desktop using temporary synthetic data and real Qt/Platform I/O."""

import argparse
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def capture(output):
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtTest import QTest
    from sim_platform.desktop.app import create_application, open_window
    from sim_platform.examples.market_demo import write_fixture
    from shiboken6 import isValid

    application = create_application()
    output.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix='market-web-capture-') as temporary:
        directory = Path(temporary)
        source = write_fixture(directory / 'fixture')
        window = open_window(directory / 'accounts', demo_source=source)
        errors = []
        window.browser.page().failed.connect(errors.append)
        window.show()

        def until(predicate, timeout=20):
            end = time.monotonic() + timeout
            while time.monotonic() < end:
                application.processEvents()
                if predicate():
                    return
                time.sleep(0.005)
            raise RuntimeError('Desktop capture timed out: ' + repr(errors))

        def js(code):
            result = []
            window.browser.page().runJavaScript(code, result.append)
            until(lambda: bool(result))
            return result[0]

        def click(selector):
            js('document.querySelector(' + json.dumps(selector) + ').click()')

        def save(name):
            # A JavaScript round trip flushes pending event delivery before Qt grabs.
            js('document.querySelector("h1").textContent')
            application.processEvents()
            QTest.qWait(100)
            window.grab().save(str(output / (name + '.png')))

        try:
            until(lambda: window.catalogue.get('accounts'))
            until(lambda: js('document.querySelectorAll(".nav").length') > 0)
            click('[data-action="data"]')
            until(lambda: js('document.querySelectorAll(".recording-toggle").length') > 0)
            click('.recording-toggle')
            save('market-data')
            click('[data-action="import"]')
            save('import')
            click('[data-action="new"]')
            save('new-experiment')
            js("document.querySelector('#setup-form').requestSubmit()")
            save('new-experiment-account')
            js("document.querySelector('#setup-form').requestSubmit()")
            save('new-experiment-review')
            for name, cash in [('Manual research', '10000'), ('Independent baseline', '20000')]:
                previous = set(window.views)
                window.dispatch(
                    'prepare',
                    dict(
                        path=str(source[0]),
                        symbol='AAPL',
                        account_name=name,
                        cash=cash,
                        policy='human',
                        execution_mode='controlled',
                        fee_per_share='.01',
                        latency_ns=0,
                        quantity=10,
                        hold_ns=2_000_000_000,
                        start_mode='first_book',
                    ),
                )
                until(
                    lambda: any(
                        key not in previous and value.get('run')
                        for key, value in window.views.items()
                    )
                )
                identity = next(key for key in window.views if key not in previous)
                click('[data-action="experiments"]')
                until(
                    lambda: js(
                        'document.querySelectorAll("[data-action=\\"open:'
                        + identity
                        + '\\"]").length'
                    )
                    > 0
                )
                click('[data-action="open:' + identity + '"]')
            until(lambda: js('document.querySelectorAll("[role=tab]").length') == 2)
            for _ in range(2):
                old_time = window.views[identity]['market']['time_ns']
                window.dispatch('session', dict(session=identity, method='step'))
                until(lambda: window.views[identity]['market']['time_ns'] != old_time)
            save('workspace')
            click('[data-action="experiments"]')
            save('experiments')
            click('[data-action="agents"]')
            click('[data-action="agent-new"]')
            save('agent-editor')
            js("document.querySelector('[name=name]').value='Market observer'")
            js(
                "document.querySelector('[name=prompt]').value='Observe the market and trade only when justified.'"
            )
            js("document.querySelector('#agent-form').requestSubmit()")
            until(lambda: bool(window.agent_catalogue.get('agents')))
            until(lambda: js('document.querySelectorAll(".agent-card").length') == 1)
            save('agents')
            window.dispatch('session', dict(session=identity, method='stop'))
            until(lambda: window.views[identity]['execution']['status'] != 'running')
            click('[data-action="experiments"]')
            run_id = window.views[identity]['run']['id']
            until(
                lambda: js(
                    'document.querySelector('
                    + json.dumps('[data-action="saved:' + run_id + '"]')
                    + ') !== null'
                )
            )
            click('[data-action="saved:' + run_id + '"]')
            until(lambda: js('document.querySelectorAll(".result-grid").length') == 1)
            save('results')
            if errors:
                raise RuntimeError(repr(errors))
        finally:
            from unittest.mock import patch

            with patch.object(window, 'confirm_close', return_value=True):
                window.close()
            until(lambda: all(not owner.thread.isRunning() for owner in window.owners.values()))
            application.processEvents()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            until(lambda: not isValid(window))
    print('Captured HTML Desktop to ' + str(output))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'build/web-desktop-preview')
    # macOS can show a recovery prompt after an unrelated Python crash. These
    # process-only Cocoa arguments are passed through, never saved as preferences.
    args, _ = parser.parse_known_args()
    capture(args.output.resolve())
