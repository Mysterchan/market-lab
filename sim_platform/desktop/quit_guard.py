"""Route OS Quit through worker shutdown without filtering other Qt objects."""

from __future__ import annotations

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid


class DesktopApplication(QApplication):
    """Keep the event loop alive until the Desktop has drained its workers."""

    def event(self, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Quit:
            window = getattr(self, '_web_research_window', None)
            if window is not None and isValid(window):
                if any(owner.thread.isRunning() for owner in window.owners.values()):
                    window.close()
                    return True
        return super().event(event)
