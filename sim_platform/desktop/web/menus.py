"""Native Desktop menus dispatch the same actions as the bundled frontend."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Dict, Optional, Union, cast

from PySide6.QtCore import QUrl
from PySide6.QtGui import QAction, QDesktopServices, QKeySequence
from PySide6.QtWidgets import QMessageBox
from PySide6.QtWebEngineCore import QWebEnginePage

from sim_platform import __version__

if TYPE_CHECKING:  # Annotation only.
    from PySide6.QtWidgets import QMenu

    from .window import WebResearchWindow

# Zoom bounds, step and the user guide address.
MIN_ZOOM = 0.8
MAX_ZOOM = 1.5
ZOOM_STEP = 0.1
DOCUMENTATION_URL = 'https://mysterchan.github.io/market-lab/user-guide/'


class DesktopMenus:
    """Own native actions and derive availability from the selected real session.

    The window creates one at startup; it owns the QActions it adds to the menu bar.
    """

    def __init__(self, window: WebResearchWindow) -> None:
        self.window = window
        self.actions: Dict[str, QAction] = {}
        self.zoom = 1.0
        bar = window.menuBar()
        file = bar.addMenu('&File')
        self.add(file, 'new', 'New experiment', 'Ctrl+N')
        self.add(file, 'import', 'Add market data…', 'Ctrl+O')
        self.add(file, 'agent-new', 'New Agent', 'Ctrl+Shift+N')
        file.addSeparator()
        self.add(file, 'export', 'Export experiment…', 'Ctrl+Shift+E')
        self.add(file, 'export-sdk', 'Export Agent SDK…')
        self.add(file, 'close-tab', 'Close experiment tab', 'Ctrl+W')
        file.addSeparator()
        self.add(file, 'settings', 'Settings…', 'Ctrl+,', role=QAction.MenuRole.PreferencesRole)
        self.add(
            file,
            'quit',
            'Quit Market Lab',
            QKeySequence.StandardKey.Quit,
            callback=window.close,
            role=QAction.MenuRole.QuitRole,
        )
        edit = bar.addMenu('&Edit')
        for key, text, shortcut, operation in (
            ('undo', 'Undo', QKeySequence.StandardKey.Undo, 'Undo'),
            ('redo', 'Redo', QKeySequence.StandardKey.Redo, 'Redo'),
            ('cut', 'Cut', QKeySequence.StandardKey.Cut, 'Cut'),
            ('copy', 'Copy', QKeySequence.StandardKey.Copy, 'Copy'),
            ('paste', 'Paste', QKeySequence.StandardKey.Paste, 'Paste'),
            ('select-all', 'Select all', QKeySequence.StandardKey.SelectAll, 'SelectAll'),
        ):
            self.add(
                edit,
                key,
                text,
                shortcut,
                callback=lambda checked=False, op=operation: window.browser.page().triggerAction(
                    getattr(QWebEnginePage.WebAction, op)
                ),
            )
        view = bar.addMenu('&View')
        self.add(view, 'search', 'Search workspace…', 'Ctrl+K')
        for key, text in (
            ('experiments', 'Experiments'),
            ('workspace', 'Trading workspace'),
            ('data', 'Market data'),
            ('agents', 'Agents'),
            ('replay', 'Recorded Replay'),
            ('comparison', 'Compare runs'),
        ):
            self.add(view, key, text)
        view.addSeparator()
        self.add(
            view,
            'zoom-in',
            'Zoom in',
            QKeySequence.StandardKey.ZoomIn,
            callback=lambda: self.set_zoom(self.zoom + ZOOM_STEP),
        )
        self.add(
            view,
            'zoom-out',
            'Zoom out',
            QKeySequence.StandardKey.ZoomOut,
            callback=lambda: self.set_zoom(self.zoom - ZOOM_STEP),
        )
        self.add(view, 'zoom-reset', 'Actual size', 'Ctrl+0', callback=lambda: self.set_zoom(1))
        self.add(view, 'theme', 'Switch appearance')
        experiment = bar.addMenu('E&xperiment')
        self.add(experiment, 'play-pause', 'Play', 'Ctrl+Return')
        self.add(experiment, 'step', 'Step market', 'Ctrl+Shift+Right')
        self.add(experiment, 'stop-confirm', 'End experiment…', 'Ctrl+Shift+Period')
        windows = bar.addMenu('&Window')
        self.add(windows, 'minimize', 'Minimize', 'Ctrl+M', callback=window.showMinimized)
        self.add(
            windows, 'fullscreen', 'Toggle full screen', 'Ctrl+Meta+F', callback=self.fullscreen
        )
        self.add(windows, 'bring-forward', 'Bring to front', callback=self.bring_forward)
        help_menu = bar.addMenu('&Help')
        self.add(
            help_menu, 'documentation', 'Market Lab documentation', callback=self.documentation
        )
        self.add(help_menu, 'licenses', 'Third-party notices', callback=self.licenses)
        self.add(
            help_menu,
            'about',
            'About Market Lab',
            callback=self.about,
            role=QAction.MenuRole.AboutRole,
        )
        self.update({})

    def add(
        self,
        menu: QMenu,
        key: str,
        text: str,
        shortcut: Optional[Union[str, QKeySequence.StandardKey]] = None,
        *,
        callback: Optional[Callable[..., Any]] = None,
        role: QAction.MenuRole = QAction.MenuRole.NoRole,
    ) -> QAction:
        """Add an action; without a callback it publishes 'ui_action' with ``key`` to the page."""
        action = QAction(text, self.window)
        action.setObjectName('desktop-' + key)
        action.setMenuRole(role)
        if shortcut is not None:
            action.setShortcut(QKeySequence(shortcut))
        action.triggered.connect(callback or (lambda: self.window.publish('ui_action', key)))
        menu.addAction(action)
        self.actions[key] = action
        return action

    def update(self, state: Dict[str, Any]) -> None:
        """Treat renderer state as a selection; derive financial readiness in Python."""
        self.window.ui_state = state
        session = state.get('session')
        view = self.window.views.get(cast(str, session), {})
        execution = view.get('execution') or {}
        ready = state.get('route') == 'workspace' and execution.get('status') == 'running'
        pipeline = view.get('pipeline')
        playing = (
            pipeline.get('state') == 'running'
            if pipeline
            else (view.get('market') or {}).get('status') == 'playing'
        )
        self.actions['play-pause'].setText('Pause' if playing else 'Play')
        self.actions['play-pause'].setEnabled(ready)
        self.actions['step'].setEnabled(ready and view.get('execution_mode') != 'live')
        self.actions['stop-confirm'].setEnabled(ready)
        self.actions['export'].setEnabled(
            bool(view.get('run')) and state.get('route') in ('workspace', 'results')
        )
        self.actions['close-tab'].setEnabled(state.get('route') == 'workspace' and bool(session))

    def set_zoom(self, value: float) -> None:
        self.zoom = round(max(MIN_ZOOM, min(MAX_ZOOM, value)), 1)
        self.window.browser.setZoomFactor(self.zoom)

    def fullscreen(self) -> None:
        self.window.showNormal() if self.window.isFullScreen() else self.window.showFullScreen()

    def bring_forward(self) -> None:
        self.window.showNormal()
        self.window.raise_()
        self.window.activateWindow()

    def documentation(self) -> None:
        QDesktopServices.openUrl(QUrl(DOCUMENTATION_URL))

    def licenses(self) -> None:
        import sys
        from pathlib import Path

        if getattr(sys, 'frozen', False):
            # _MEIPASS is set by the bundler in a frozen app; the stubs do not declare it.
            path = Path(cast(Any, sys)._MEIPASS) / 'licenses/THIRD_PARTY_NOTICES.md'
        else:
            path = Path(__file__).resolve().parents[3] / 'THIRD_PARTY_NOTICES.md'
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def about(self) -> None:
        QMessageBox.about(
            self.window,
            'About Market Lab',
            f'Market Lab {__version__}\nHistorical market research and simulated trading.\n\nLocal workspace. No broker connection.',
        )
