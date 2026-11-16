"""Optional native app entry; --demo uses temporary synthetic data and accounts."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import sys
from typing import TYPE_CHECKING, Any, List, Optional, Tuple, cast

if TYPE_CHECKING:  # Annotation only.
    from PySide6.QtWidgets import QApplication

    from .web.window import WebResearchWindow

APPLICATION_NAME = 'Market Lab'


def create_application(arguments: Optional[List[str]] = None) -> QApplication:
    """Create or reuse Qt with WebEngine's shared contexts configured before startup.

    Call this on the main thread before constructing Desktop widgets. Qt requires
    AA_ShareOpenGLContexts before QApplication exists, even when WebEngine widgets
    are imported later. Tests and capture/benchmark tools use this same entry point.
    Graphical backend selection remains the responsibility of the launch environment.
    """
    from PySide6.QtCore import QCoreApplication, Qt
    from PySide6.QtWidgets import QApplication

    application = QApplication.instance()
    if application is None:
        QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
        QCoreApplication.setApplicationName(APPLICATION_NAME)
        from .quit_guard import DesktopApplication

        application = DesktopApplication([] if arguments is None else arguments)
        from sim_platform import __version__

        application.setApplicationDisplayName(APPLICATION_NAME)
        application.setApplicationVersion(__version__)
    return cast('QApplication', application)


def launch(
    directory: Optional[Any] = None, library: Optional[Any] = None, demo: bool = False
) -> int:
    """Run the desktop until it quits; return the Qt exit code."""
    from sim_platform.persistence.paths import data_directory

    application = create_application(sys.argv[:1])
    application.setApplicationName(APPLICATION_NAME)
    if demo:
        from sim_platform.examples.market_demo import write_fixture

        with TemporaryDirectory(prefix='trading-desktop-demo-') as temporary:
            source = write_fixture(Path(temporary) / 'fixture')
            window = open_window(Path(temporary) / 'accounts', library, source)
            window.show()
            return application.exec()
    window = open_window(directory or data_directory(), library)
    window.show()
    return application.exec()


def open_window(
    directory: Any, library: Optional[Any] = None, demo_source: Optional[Tuple[Any, Any]] = None
) -> WebResearchWindow:
    """Create the HTML Desktop once; Python workers retain all financial ownership."""
    from PySide6.QtWidgets import QApplication
    from shiboken6 import isValid
    from .web.window import WebResearchWindow

    application = QApplication.instance()
    if application is None:
        raise RuntimeError('create QApplication before opening the desktop')
    current = getattr(application, '_web_research_window', None)
    if current is not None and isValid(current) and not current._closing:
        current.show()
        current.raise_()
        current.activateWindow()
        return current
    window = WebResearchWindow(directory, library, demo_source)
    # The window is remembered on the application object for reuse.
    cast(Any, application)._web_research_window = window
    return window
