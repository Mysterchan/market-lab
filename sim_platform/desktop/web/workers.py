"""GUI-thread relays for independently owned research workers and queued commands."""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from PySide6.QtCore import QObject, QThread, Qt, Signal, Slot
from sim_platform.desktop.runtime import DesktopRuntime
from sim_platform.desktop.worker import ResearchWorker


class WorkerOwner(QObject):
    """Queue commands to one worker and release it before destroying its thread.

    The window creates one per open experiment (plus the 'manager'); it owns the
    QThread and its ResearchWorker and relays worker results as ``event`` signals.
    """

    command = Signal(str, object)
    quitting = Signal()
    cancel = Signal()
    closed = Signal()
    # Not a bug: the Qt stubs declare QObject.event()/thread() methods; this class shadows the name.
    event: Signal = Signal(str, object)  # type: ignore[assignment]
    # The worker's thread (set in __init__); shadows QObject.thread() for the same reason.
    thread: QThread  # type: ignore[assignment]
    catalogued = Signal(object)

    def __init__(
        self,
        identity: str,
        directory: Any,
        library: Optional[Any],
        demo_source: Optional[Tuple[Any, Any]] = None,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self.identity = identity
        self.thread = QThread(self)
        self.worker = ResearchWorker(DesktopRuntime(directory, library, demo_source))
        self.worker.moveToThread(self.thread)
        self.command.connect(self.worker.execute, Qt.ConnectionType.QueuedConnection)
        self.quitting.connect(self.worker.shutdown, Qt.ConnectionType.QueuedConnection)
        self.cancel.connect(self.worker.cancel_job, Qt.ConnectionType.DirectConnection)
        self.thread.started.connect(self.worker.initialize)
        self.worker.updated.connect(self.view)
        self.worker.catalogued.connect(self.catalog)
        self.worker.datasets_ready.connect(self.datasets)
        self.worker.agents_ready.connect(self.agents)
        self.worker.failed.connect(self.error)
        self.worker.message.connect(self.notice)
        self.worker.task_changed.connect(self.task)
        self.worker.replay_ready.connect(self.replay)
        self.worker.compared.connect(self.comparison)
        self.worker.evaluated.connect(self.evaluation)
        self.worker.agent_saved.connect(self.saved)
        self.worker.reset_completed.connect(self.reset)
        self.worker.job_changed.connect(self.busy)
        self.worker.finished.connect(self.worker.deleteLater)
        self.worker.finished.connect(self.thread.quit, Qt.ConnectionType.DirectConnection)
        self.thread.finished.connect(self.closed)

    @Slot(object)
    def view(self, value: Dict[str, Any]) -> None:
        self.event.emit('view', dict(session=self.identity, value=value))

    @Slot(object)
    def catalog(self, value: Dict[str, Any]) -> None:
        self.event.emit('catalog', dict(session=self.identity, value=value))
        self.catalogued.emit(value)

    @Slot(object)
    def datasets(self, value: Dict[str, Any]) -> None:
        self.event.emit('datasets', value)

    @Slot(object)
    def agents(self, value: Dict[str, Any]) -> None:
        self.event.emit('agents', value)

    @Slot(str)
    def error(self, message: str) -> None:
        self.event.emit('error', dict(session=self.identity, message=message))

    @Slot(str)
    def notice(self, message: str) -> None:
        if message not in (
            'Action completed',
            'Dataset library refreshed',
            'Task complete',
            'Ready · press Play to start.',
            'Ready · Create an experiment or view the market replay.',
        ):
            self.event.emit('notice', message)

    @Slot(object)
    def task(self, value: Dict[str, Any]) -> None:
        self.event.emit('task', dict(session=self.identity, **value))

    @Slot(object)
    def replay(self, value: Any) -> None:
        self.event.emit('replay', value)

    @Slot(object)
    def comparison(self, value: Any) -> None:
        self.event.emit('comparison', value)

    @Slot(object)
    def evaluation(self, value: Any) -> None:
        self.event.emit('evaluation', value)

    @Slot(object)
    def saved(self, value: Any) -> None:
        self.event.emit('agent_saved', value)

    @Slot(object)
    def reset(self, value: Dict[str, Any]) -> None:
        self.event.emit('reset', value)

    @Slot(bool)
    def busy(self, value: bool) -> None:
        self.event.emit('busy', dict(session=self.identity, busy=value))

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.cancel.emit()
        self.quitting.emit()
