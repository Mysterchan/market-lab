"""Opt-in packaged-App check using temporary synthetic data, never normal storage."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import time
from typing import Any, Dict, List, Optional, Union, cast

# Give up if the packaged check has not finished after this many seconds.
CHECK_TIMEOUT_SECONDS = 60
# Interval (ms) of the check's state machine and of its close polling.
POLL_INTERVAL_MS = 30


def run_smoke(destination: Union[str, Path]) -> int:
    """Verify bundled rendering, menus, market subprocess, order and saved result."""
    from PySide6.QtCore import QTimer
    from shiboken6 import isValid
    from sim_platform.desktop.app import create_application, open_window
    from sim_platform.examples.market_demo import write_fixture
    from sim_platform.market.native import resolve_library_path

    application = create_application()
    application.setQuitOnLastWindowClosed(False)
    destination = Path(destination).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix='market-lab-app-check-') as temporary:
        root = Path(temporary)
        source = write_fixture(root / 'fixture')
        window = open_window(root / 'accounts', demo_source=source)
        cast(Any, window).confirm_close = lambda: True
        window.show()
        state: Dict[str, Any] = dict(phase='startup', started=time.monotonic(), exit_code=0)
        errors: List[Any] = []
        cast(Any, window.browser.page()).failed.connect(errors.append)
        window.browser.commands.event.connect(
            lambda encoded: (
                errors.append(json.loads(encoded)['value'])
                if json.loads(encoded)['kind'] == 'error'
                else None
            )
        )
        timer = QTimer(application)
        timer.setInterval(POLL_INTERVAL_MS)

        def finish(error: Optional[BaseException] = None) -> None:
            timer.stop()
            if error:
                state['exit_code'] = 1
            report = dict(
                ok=error is None,
                error=str(error) if error else None,
                frozen=bool(getattr(sys, 'frozen', False)),
                native_library=str(resolve_library_path()),
                application=application.applicationName(),
                menus=[a.text() for a in window.menuBar().actions()],
                phase=state['phase'],
                errors=errors,
            )
            view = window.views.get(cast(str, state.get('session')), {})
            report['execution_status'] = (view.get('execution') or {}).get('status')
            report['orders'] = len((view.get('account') or {}).get('orders', []))
            report['fills'] = sum(e['kind'] == 'fill' for e in view.get('events', []))
            report['run_id'] = (view.get('run') or {}).get('id')
            from sim_platform.persistence.agents.sdk import SDKFiles
            import zipfile

            wheel = SDKFiles(root / 'sdk-check').wheel()
            with zipfile.ZipFile(wheel) as archive:
                report['sdk_wheel'] = wheel.name
                report['sdk_valid'] = archive.testzip() is None
            if report['frozen']:
                import subprocess

                # _MEIPASS is set by the bundler in a frozen app; not in the stubs.
                helper = Path(cast(Any, sys)._MEIPASS) / 'helpers/uv'
                import ssl

                report['ca_certificates'] = len(ssl.create_default_context().get_ca_certs())
                report['python_manager'] = subprocess.run(
                    [str(helper), '--version'], check=True, capture_output=True, text=True
                ).stdout.strip()
            # destination was resolved to a Path above.
            target = cast(Path, destination)
            target.write_text(json.dumps(report, indent=2) + '\n')
            window.grab().save(str(target.with_suffix('.png')))
            window.close()

            def drained() -> None:
                if not isValid(window):
                    application.exit(state['exit_code'])
                else:
                    QTimer.singleShot(POLL_INTERVAL_MS, drained)

            QTimer.singleShot(POLL_INTERVAL_MS, drained)

        def tick() -> None:
            try:
                if time.monotonic() - state['started'] > CHECK_TIMEOUT_SECONDS:
                    raise TimeoutError('Packaged App did not finish its check in 60 seconds')
                if errors:
                    raise RuntimeError(str(errors))
                phase = state['phase']
                if phase == 'startup' and window.catalogue.get('accounts') and window.ui_state:
                    window.menus.actions['new'].trigger()
                    state['phase'] = 'rendering'
                elif phase == 'rendering' and window.ui_state.get('route') == 'new':
                    state['phase'] = 'preparing'
                    window.dispatch(
                        'prepare',
                        dict(
                            path=str(source[0]),
                            symbol='AAPL',
                            account_name='Packaged check',
                            cash='10000',
                            policy='human',
                            execution_mode='controlled',
                            fee_per_share='.01',
                            latency_ns=0,
                            quantity=10,
                            hold_ns=2_000_000_000,
                            start_mode='first_book',
                        ),
                    )
                elif phase == 'preparing':
                    found = next(
                        (
                            key
                            for key, v in window.views.items()
                            if key != 'manager' and v.get('run')
                        ),
                        None,
                    )
                    if found:
                        state['session'] = found
                        state['phase'] = 'ordering'
                        window.dispatch(
                            'session',
                            dict(
                                session=found,
                                method='order',
                                client_id='packaged-check',
                                side='buy',
                                quantity=10,
                                limit_price='190.10',
                            ),
                        )
                elif phase == 'ordering':
                    view = window.views[state['session']]
                    if view.get('account', {}).get('orders'):
                        state['phase'] = 'stepping'
                        state['time'] = view['market']['time_ns']
                        window.dispatch('session', dict(session=state['session'], method='step'))
                elif phase == 'stepping':
                    view = window.views[state['session']]
                    if view['market']['time_ns'] != state['time']:
                        state['phase'] = 'stopping'
                        window.dispatch('session', dict(session=state['session'], method='stop'))
                elif phase == 'stopping':
                    view = window.views[state['session']]
                    if view['execution']['status'] != 'running':
                        state['phase'] = 'saved'
                        window.dispatch('open_saved', dict(run_id=view['run']['id']))
                elif phase == 'saved' and window.views.get('manager', {}).get('mode') == 'saved':
                    if not window.views[state['session']]['account']['orders']:
                        raise RuntimeError('Order was not persisted')
                    if not any(
                        event['kind'] == 'fill'
                        for event in window.views[state['session']].get('events', [])
                    ):
                        raise RuntimeError('Expected fixture order did not fill')
                    state['phase'] = 'complete'
                    finish()
            except Exception as exc:
                finish(exc)

        timer.timeout.connect(tick)
        timer.start()
        return application.exec()
