"""Qt bridge to platform-created controllers, libraries and background jobs."""

from __future__ import annotations
import logging

from typing import TYPE_CHECKING, Any, Dict, Optional, cast

from PySide6.QtCore import QObject, QTimer, Signal, Slot
from sim_platform.resources import cleanup_after_error

_log = logging.getLogger(__name__)

if TYPE_CHECKING:  # Annotation only.
    from sim_platform.research.batch import ResearchBatch
    from .runtime import DesktopRuntime

# Replay and job poll interval (ms): bounded work per turn, one render per turn.
TICK_INTERVAL_MS = 50
# Execution states on the way to another: the view keeps refreshing until they settle.
SETTLING_STATES = ('pausing', 'ending')
# Events and time budget for one replay turn.
TICK_MAX_EVENTS = 5000
TICK_BUDGET_SECONDS = 0.035


SESSION_COMMANDS = frozenset(
    (
        'catalog',
        'compare',
        'export_comparison',
        'create_profile',
        'create_account',
        'load',
        'start',
        'start_from_beginning',
        'play',
        'pause',
        'step',
        'stop',
        'order',
        'cancel',
        'handoff',
        'view_run',
        'recover_stop',
        'export',
    )
)


class ResearchWorker(QObject):
    """One desktop experiment's worker, living on its own QThread.

    WorkerOwner creates it with a DesktopRuntime and moves it to a thread;
    initialize() opens the runtime there, execute() runs commands, and shutdown()
    closes the runtime and emits ``finished``. The session and job attributes are
    the runtime's, and are typed Any because they are set only after initialize().
    """

    updated = Signal(object)
    catalogued = Signal(object)
    message = Signal(str)
    failed = Signal(str)
    finished = Signal()
    compared = Signal(object)
    evaluated = Signal(object)
    datasets_ready = Signal(object)
    reset_completed = Signal(object)
    job_changed = Signal(bool)
    agents_ready = Signal(object)
    agent_saved = Signal(object)
    task_changed = Signal(object)
    replay_ready = Signal(object)

    def __init__(self, runtime: DesktopRuntime) -> None:
        super().__init__()
        self.runtime = runtime
        # Created in initialize() on the worker thread.
        self.timer: Any = None
        self.job_timer: Any = None
        self._pending_start: Optional[Dict[str, Any]] = None
        self._comparison: Optional[Dict[str, Any]] = None
        from .tasks import DesktopTasks

        self.tasks = DesktopTasks(self)

    @property
    def session(self) -> Any:
        """The runtime's research controller (None until initialized)."""
        return self.runtime.session

    @property
    def job(self) -> Any:
        """The runtime's background job (None until initialized)."""
        return self.runtime.job

    @property
    def batch(self) -> Optional[ResearchBatch]:
        return self.runtime.batch

    @property
    def datasets(self) -> Any:
        """The runtime's dataset library (None until initialized)."""
        return self.runtime.datasets

    @Slot()
    def initialize(self) -> None:
        try:
            self.runtime.initialize()
            self.timer = QTimer(self)
            self.timer.setInterval(TICK_INTERVAL_MS)
            self.timer.timeout.connect(self._tick)
            self.job_timer = QTimer(self)
            self.job_timer.setInterval(TICK_INTERVAL_MS)
            self.job_timer.timeout.connect(self._poll_job)
            self.catalogued.emit(self.session.catalog())
            self.updated.emit(self.session.view())
            self.message.emit('Ready · Create an experiment or view the market replay.')
            try:
                self.datasets_ready.emit(self.datasets.catalog())
            except Exception as exc:
                _log.warning('Handled failure in initialize', exc_info=True)
                self.failed.emit(str(exc))
        except Exception as exc:
            _log.warning('Handled failure in initialize', exc_info=True)
            cleanup_after_error(exc, self.runtime.close)
            self.failed.emit(str(exc))

    def _admit_command(self, method: str) -> bool:
        """Apply existing busy/preparation gates; Stop cancels the current long job."""
        if self.tasks.busy:
            if method == 'stop':
                self.tasks.cancel()
                return False
            raise ValueError('Wait for preparation or cancel it first')
        preparing = (self.session.preparation or {}).get('status') == 'preparing'
        if preparing:
            if method == 'stop':
                self.timer.stop()
                self._pending_start = None
                self.updated.emit(self.session.cancel_preparation())
                self.message.emit('Market preparation cancelled. No experiment was created.')
                return False
            if method not in ('catalog', 'datasets'):
                raise ValueError('Cancel market preparation before changing the experiment')
        if self.job.active:
            if method == 'stop':
                self.job.cancel()
                self.message.emit('Cancelling comparison…')
                return False
            if method not in ('catalog', 'datasets'):
                raise ValueError('Finish or cancel the comparison before changing the experiment')
        return True

    def _reset_local_data(self) -> None:
        """Reset through the owned runtime, restoring the poll timer if reset fails."""
        was_playing = self.timer.isActive()
        self.timer.stop()
        try:
            self.runtime.reset()
            self._comparison = None
        except Exception:
            if was_playing and self.session.market and self.session.market.status == 'playing':
                self.timer.start()
            raise
        # One delivery lets the GUI replace the old run before exposing reset
        # completion, without interleaving separate catalogue/view updates.
        self.reset_completed.emit(
            dict(
                catalog=self.session.catalog(),
                view=self.session.view(),
                datasets=self.datasets.catalog(),
            )
        )
        self.message.emit('Local data reset to the first-launch state.')

    def _datasets_command(self, method: str, arguments: Dict[str, Any]) -> None:
        """Update/query the dataset library and emit its resulting catalogue."""
        if method == 'datasets':
            self.datasets_ready.emit(self.datasets.catalog(**arguments))
            self.message.emit('Dataset library refreshed')
            return
        operation = {
            'add_dataset': self.datasets.add,
            'locate_dataset': self.datasets.locate,
            'dataset_storage': self.datasets.set_storage,
        }[method]
        self.datasets_ready.emit(operation(**arguments))
        self.message.emit(
            'Dataset added to saved data' if method == 'add_dataset' else 'Dataset library updated'
        )

    def _start_comparison(self, arguments: Dict[str, Any]) -> None:
        """Start a background comparison only after the current simulation stops."""
        if self.session.active:
            raise ValueError('Stop the current simulation before running a comparison')
        self.timer.stop()
        if self.session.market and self.session.market.status == 'playing':
            self.session.pause()
        self.job.start(
            cast('ResearchBatch', self.batch).compare, include_details=False, **arguments
        )
        self.job_timer.start()
        self.job_changed.emit(True)
        self.message.emit('Comparison running…')

    def _prepare_source(self, method: str, arguments: Dict[str, Any]) -> None:
        """Keep pending start options separate from cancellable market preparation."""
        self.timer.stop()
        source_keys = (
            'path',
            'symbol',
            'news',
            'allow_implicit_eof',
            'start_mode',
            'start_time_ns',
        )
        source = {key: value for key, value in arguments.items() if key in source_keys}
        self._pending_start = (
            {key: value for key, value in arguments.items() if key not in source_keys}
            if method == 'start_from_beginning'
            else None
        )
        try:
            view = self.session.begin_prepare(**source)
            self._publish_preparation(view)
        except Exception:
            self._pending_start = None
            raise

    def _publish_result(self, method: str, arguments: Dict[str, Any], result: Any) -> None:
        """Apply timer/catalog/view updates after a successful session command."""
        settling = (
            isinstance(result, dict)
            and (result.get('pipeline') or {}).get('state') in SETTLING_STATES
        )
        if method == 'play' or settling:
            self.timer.start()
        elif method in ('load', 'view_run', 'recover_stop'):
            self.timer.stop()
        if method in (
            'create_profile',
            'create_account',
            'start',
            'start_from_beginning',
            'stop',
            'recover_stop',
        ):
            profile = (
                result
                if method == 'create_profile'
                else arguments.get('profile', self.session.profile)
            )
            self.catalogued.emit(self.session.catalog(profile))
        self.updated.emit(
            result if isinstance(result, dict) and 'mode' in result else self.session.view()
        )
        if method == 'export':
            self.message.emit('Exported: ' + result)
        elif method == 'handoff':
            self.message.emit(
                'Decision-maker switch scheduled'
                if arguments.get('condition')
                else 'Decision maker changed'
            )
        else:
            self.message.emit('Action completed')

    def _report_error(self, error: Exception) -> None:
        """Publish the last readable view without replacing the command's failure."""
        if self.session:
            try:
                self.updated.emit(self.session.view())
            except Exception:
                _log.debug('Handled failure in _report_error', exc_info=True)
        self.failed.emit(str(error))

    def _execute_session_command(self, method: str, arguments: Dict[str, Any]) -> None:
        """Route a known session command through its existing controls and handlers."""
        if method not in SESSION_COMMANDS:
            raise ValueError('Unknown operation')
        if method == 'play' and self.session.market and self.session.market.status == 'playing':
            return
        if method in ('pause', 'stop'):
            self.timer.stop()
            if self.session.market and self.session.market.status == 'playing':
                self.session.pause()
        if method == 'catalog':
            catalog = self.session.catalog(**arguments)
            self.catalogued.emit(catalog)
            self.tasks.catalog(catalog['profile'])
            return
        if method == 'compare':
            self._start_comparison(arguments)
            return
        if method == 'export_comparison':
            if self._comparison is None:
                raise ValueError('Run a comparison before exporting results')
            self.message.emit(
                'Exported: '
                + cast('ResearchBatch', self.batch).export(self._comparison, **arguments)
            )
            return
        if method == 'load' or (method == 'start_from_beginning' and 'path' in arguments):
            self._prepare_source(method, arguments)
            return
        result = getattr(self.session, method)(**arguments)
        self._publish_result(method, arguments, result)

    @Slot(str, object)
    def execute(self, method: str, arguments: Dict[str, Any]) -> None:
        """Admit a command, call its handler, and publish a view or failure."""
        try:
            if self.session is None:
                raise ValueError('Research engine is not ready yet')
            if method in self.tasks.commands:
                self.tasks.execute(method, arguments)
                return
            if not self._admit_command(method):
                return
            if method == 'reset_local_data':
                self._reset_local_data()
            elif method in ('add_dataset', 'locate_dataset', 'dataset_storage', 'datasets'):
                self._datasets_command(method, arguments)
            else:
                self._execute_session_command(method, arguments)
        except Exception as exc:
            _log.warning('Handled failure in execute', exc_info=True)
            self._report_error(exc)

    @Slot()
    def _tick(self) -> None:
        try:
            if (self.session.preparation or {}).get('status') == 'preparing':
                self._publish_preparation(self.session.prepare_tick())
                return
            # Preserve every execution/decision boundary while removing the
            # one-message-per-50ms cap. Yield regularly for pause/end commands.
            view = self.session.tick(max_events=TICK_MAX_EVENTS, budget_seconds=TICK_BUDGET_SECONDS)
            self.updated.emit(view)
            if (view.get('pipeline') or {}).get('state') in SETTLING_STATES:
                # Keep polling until the owner reaches paused, completed, stopped or failed,
                # so the published view never stays on a passing state.
                return
            playing = (
                view['pipeline']['state'] == 'running'
                if view.get('pipeline')
                else view['market']['status'] == 'playing'
            )
            if not playing or (view['execution'] and view['execution']['status'] != 'running'):
                self.timer.stop()
                if self.session.profile:
                    self.catalogued.emit(self.session.catalog(self.session.profile))
        except Exception as exc:
            _log.warning('Handled failure in _tick', exc_info=True)
            self.timer.stop()
            self._pending_start = None
            try:
                self.updated.emit(self.session.view())
            except Exception:
                _log.debug('Handled failure in _tick', exc_info=True)
            self.failed.emit(str(exc))

    def _publish_preparation(self, view: Dict[str, Any]) -> None:
        if view['preparation']['status'] == 'preparing':
            self.updated.emit(view)
            self.message.emit('Preparing market data… You can cancel while it is loading.')
            self.timer.start()
            return
        self.timer.stop()
        arguments, self._pending_start = self._pending_start, None
        if arguments is not None:
            view = self.session.start(**arguments)
            self.catalogued.emit(self.session.catalog(self.session.profile))
        self.updated.emit(view)
        self.message.emit('Market ready at the selected start point · Paused')

    @Slot()
    def cancel_job(self) -> None:
        # Safe for a direct cross-thread signal: only a threading.Event is mutated.
        if self.job is not None:
            self.tasks.cancel()

    @Slot()
    def _poll_job(self) -> None:
        if self.tasks.busy:
            self.tasks.poll()
            return
        if self.job.future is None or not self.job.future.done():
            return
        self.job_timer.stop()
        try:
            result = self.job.take_result()
            self._comparison = result
            self.compared.emit(result)
            self.message.emit(
                'Comparison cancelled'
                if result.get('status') == 'cancelled'
                else 'Comparison complete. Each strategy used a fresh account with the same starting capital.'
            )
        except Exception as exc:
            _log.warning('Handled failure in _poll_job', exc_info=True)
            self.failed.emit(str(exc))
        finally:
            self.job_changed.emit(False)
            if self.session:
                self.updated.emit(self.session.view())

    @Slot()
    def shutdown(self) -> None:
        if self.timer:
            self.timer.stop()
        if self.job_timer:
            self.job_timer.stop()
        try:
            self.tasks.cancel()
            self.runtime.close()
        except Exception as exc:
            _log.warning('Handled failure in shutdown', exc_info=True)
            self.failed.emit(str(exc))
        finally:
            self.finished.emit()
