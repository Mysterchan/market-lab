"""Local document bridge with explicit commands and detached JSON event delivery."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from PySide6.QtCore import QObject, QUrl, Signal, Slot
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView


COMMANDS = frozenset(
    (
        'reset_local_data',
        'dataset_storage',
        'recover_stop',
        'ui_state',
        'refresh',
        'pick_file',
        'add_dataset',
        'locate_dataset',
        'prepare',
        'load_market',
        'session',
        'open_saved',
        'create_account',
        'create_profile',
        'agents',
        'save_agent',
        'prepare_environment',
        'export',
        'export_sdk',
        'export_comparison',
        'prepare_replay',
        'verify_run',
        'compare',
        'evaluation_runs',
        'evaluate',
        'export_evaluation',
        'select_profile',
        'save_credential',
        'load_credential',
        'cancel_preparation',
        'cancel_task',
    )
)


# Largest encoded command accepted from the page (256 KiB).
MAX_COMMAND_BYTES = 262144


class FrontendCommands(QObject):
    """Expose only named UI operations; the window validates their arguments.

    The FrontendView owns one and registers it on its web channel.
    """

    requested = Signal(str, object)
    # Not a bug: the Qt stubs declare QObject.event()/thread() methods; this class shadows the name.
    event: Signal = Signal(str)  # type: ignore[assignment]

    @Slot(str, str)
    def invoke(self, name: str, encoded: str) -> None:
        if name not in COMMANDS or len(encoded) > MAX_COMMAND_BYTES:
            return
        try:
            value = json.loads(encoded)
        except (ValueError, TypeError):
            return
        if isinstance(value, dict):
            self.requested.emit(name, value)

    def publish(self, kind: str, value: Any) -> None:
        self.event.emit(json.dumps(dict(kind=kind, value=value), ensure_ascii=True))


class FrontendPage(QWebEnginePage):
    """Keep the privileged channel on the bundled document only.

    Owned by its FrontendView; ``document_url`` is set right after construction.
    """

    failed = Signal(str)
    document_url: QUrl

    def javaScriptConsoleMessage(
        self,
        level: QWebEnginePage.JavaScriptConsoleMessageLevel,
        message: str,
        line: int,
        source: str,
    ) -> None:
        if level == QWebEnginePage.JavaScriptConsoleMessageLevel.ErrorMessageLevel:
            self.failed.emit('Desktop rendering failed at line ' + str(line) + ': ' + message)
        super().javaScriptConsoleMessage(level, message, line, source)

    # Not a bug: Qt always passes a QUrl here; the stubs also allow str.
    def acceptNavigationRequest(  # type: ignore[override]
        self, url: QUrl, kind: QWebEnginePage.NavigationType, main_frame: bool
    ) -> bool:
        return url == self.document_url or url.toString() == 'about:blank'

    # Not a bug: Qt accepts a null page here (no new window); the stubs omit Optional.
    def createWindow(  # type: ignore[override]
        self, kind: QWebEnginePage.WebWindowType
    ) -> Optional[QWebEnginePage]:
        return None


class FrontendView(QWebEngineView):
    """Own the bundled page and channel; never read or execute remote content.

    The window creates one and keeps it for its lifetime.
    """

    def __init__(self, parent: Optional[Any] = None) -> None:
        super().__init__(parent)
        page = FrontendPage(self)
        page.document_url = QUrl.fromLocalFile(
            str(Path(__file__).with_name('frontend') / 'index.html')
        )
        self.setPage(page)
        self.settings().setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False
        )
        self.commands = FrontendCommands(self)
        self.channel = QWebChannel(self)
        self.channel.registerObject('desktop', self.commands)
        page.setWebChannel(self.channel)
        self.load(page.document_url)
