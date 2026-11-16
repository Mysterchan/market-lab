"""Shared prepared execution lifecycle used by method-specific DecisionRunners.

The owner merges source events and admitted commands; it never reconstructs a
native book. Result formatting/JSON/IPC delivery occur on the output stage.
This class is internal infrastructure, not another public experiment root.
"""

from __future__ import annotations
import logging

from concurrent.futures import Future
from queue import Queue, Empty
from threading import Event, RLock, Thread, get_ident
from time import perf_counter_ns
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Protocol, Tuple, Union, cast

from sim_platform.experiments.scheduling.clock import real_delay_ns
from sim_platform.domain.models import ContractError
from sim_platform.domain.json_values import copy_json
from sim_platform.experiments.execution.timeline.sequencer import Sequencer
from sim_platform.experiments.execution.timeline.runner import ExecutionTimeline
from sim_platform.experiments.execution.metrics import RuntimeMetrics
from sim_platform.domain.operation_position import OperationPosition
from sim_platform.experiments.execution.delivery import ResultDelivery, running_future
from .controls import CONTROL_ACK_TIMEOUT_SECONDS, EXECUTION_DRAIN_TIMEOUT_SECONDS

_log = logging.getLogger(__name__)

if TYPE_CHECKING:  # Annotation only.
    from sim_platform.market.models import MarketEvent
    from sim_platform.market.prepared.session import PreparedSession
    from sim_platform.experiments.execution.delivery import Sink
    from sim_platform.experiments.execution.market import LocalMarketExecution
    from sim_platform.experiments.execution.timeline.runner import TimelineReader
    from sim_platform.experiments.execution.timeline.sequencer import Command, CommandResult
    from sim_platform.experiments.scheduling.clock import ExperimentClock, SharedExperimentClock

    # The run clock: in-process for policy runs, shared with children for Agent runs.
    RunClock = Union[ExperimentClock, SharedExperimentClock]

# Default bounds: admitted commands awaiting results, and allowed execution lag.
DEFAULT_COMMAND_CAPACITY = 64
DEFAULT_LAG_LIMIT_NS = 100_000_000
# Longest idle wait of the owner loop between turns.
MAX_IDLE_WAIT_SECONDS = 0.05
NS_PER_SECOND = 1e9
# Fewest consecutive idle events worth advancing as one span.
MIN_IDLE_SPAN = 2

# Wire position: 'sequence', 'time_ns', 'boundary' and 'ordinal' as decimal text.
WirePosition = Dict[str, str]
# Performs one admitted command: operation(payload, position) returns its result.
Operation = Callable[[Any, WirePosition], Any]
# Captures a market read: read(request, position, receipt_id) returns its result.
ReadOperation = Callable[[Any, WirePosition, str], Any]


class Lifecycle(Protocol):
    """The run's scheduling binding (RuntimeBinding in application): it is told about
    financial changes, owner turns and the end, and publishes through ``notice``."""

    @property
    def notice(self) -> Callable[[str, Dict[str, Any]], None]:
        """Publishes a notice: notice(event, details); the session replaces it."""

    @notice.setter
    def notice(self, value: Callable[[str, Dict[str, Any]], None]) -> None: ...

    def observe(self, position: WirePosition) -> None: ...

    def step(self, position: WirePosition) -> None: ...

    def end(self, reason: str) -> None: ...


# Execution lifecycle only. Work/attempt and participant acknowledgements have
# separate owners; their states must not overwrite this financial lifecycle.
EXECUTION_TRANSITIONS = {
    'ready': {'running', 'ending'},
    'running': {'pausing', 'ending'},
    'pausing': {'paused', 'ending'},
    'paused': {'running', 'ending'},
    'ending': {'completed', 'stopped', 'failed'},
    'completed': set(),
    'stopped': set(),
    'failed': set(),
}


class ExperimentSession:
    """The background financial owner of one experiment run.

    Assembly creates it ready and borrows it the driver, reader and clock; it creates
    its Sequencer, ExecutionTimeline and ResultDelivery. start() launches the
    'execution-owner' thread, the only thread that changes financial state. close()
    stops the run and waits for the owner and output threads; it keeps ownership
    (raises) if either does not stop in time.

    Args:
        driver: Market execution port for the run's market and ExecutionSession.
        reader: Prepared source event index.
        clock: Experiment clock; paused until play().
        start_ns: Experiment start time.
        end_ns: Experiment end time (exclusive), within the prepared source.
        sink: Receives delivered output messages.
        operation: Performs admitted commands; may be rebound by bind_ready().
        capacity: Most admitted commands awaiting results.
        lag_limit_ns: Largest allowed gap between experiment time and execution.
        lag_window_ns: How long the lag may exceed the limit before the run fails.
        replay_seed: Published as 'prepared_run' when the run starts.
        terminal_evidence: Returns extra values for the 'prepared_final' notice.
    """

    # Set in __init__; declared here because _transition is defined first.
    _state: str
    channels: ResultDelivery

    def _transition(self, state: str) -> None:
        """Change lifecycle at an owner/control safe point; terminal is final."""
        if state == self._state:
            return
        if state not in EXECUTION_TRANSITIONS[self._state]:
            raise ContractError('invalid execution lifecycle transition')
        self._state = state

    def __init__(
        self,
        driver: LocalMarketExecution,
        reader: TimelineReader,
        *,
        clock: RunClock,
        start_ns: int,
        end_ns: int,
        sink: Sink,
        operation: Operation,
        capacity: int = DEFAULT_COMMAND_CAPACITY,
        lag_limit_ns: int = DEFAULT_LAG_LIMIT_NS,
        lag_window_ns: int = 0,
        replay_seed: Optional[Dict[str, Any]] = None,
        terminal_evidence: Optional[Callable[[], Dict[str, Any]]] = None,
    ) -> None:
        if not start_ns < end_ns <= reader.manifest['last_ns'] + 1:
            raise ContractError('experiment exceeds prepared source coverage')
        self.driver, self.reader, self.clock = driver, reader, clock
        self.end_ns, self.operation = end_ns, operation
        self.lag_limit_ns, self.lag_window_ns = lag_limit_ns, lag_window_ns
        self._replay_seed = copy_json(replay_seed)
        self._terminal_evidence: Callable[[], Dict[str, Any]] = terminal_evidence or (dict)
        self._wake, self._lock = Event(), RLock()
        self._controls: Queue[Tuple[str, Optional[Future[Any]]]] = Queue(4)
        self._thread: Optional[Thread] = None
        self._current: Optional[Command] = None
        self._state = 'ready'
        self._error: Optional[str] = None
        self._cleanup_errors: List[str] = []
        self._ordinal = 0
        self._stream_sealed = False
        self._receipt_number = 0
        self._read_operation: Optional[ReadOperation] = None
        self._pending: Dict[str, Future[Any]] = {}
        self._on_close: Callable[[], None] = lambda: None
        self._cancel_queries: Callable[[], None] = lambda: None
        self.lifecycle: Optional[Lifecycle] = None
        self._account_version: Optional[int] = None
        self._lag_since: Optional[int] = None
        self.metrics = RuntimeMetrics(lag_limit_ns)
        self.sequencer = Sequencer(
            clock.target_ns, start_ns=start_ns, end_ns=end_ns, capacity=capacity
        )
        self.timeline = ExecutionTimeline(
            reader,
            self.sequencer,
            self._advance,
            self._apply,
            self._finish,
            start_ns=start_ns,
            advance_span=self._advance_span,
            after_market=lambda: self._observe_financial(self._position()),
        )
        self.channels = ResultDelivery(capacity, sink, self._wake)

    def _position(
        self, time_ns: Optional[int] = None, ordinal: Optional[int] = None
    ) -> WirePosition:
        return OperationPosition(
            sequence=self.driver.sequence,
            time_ns=self.timeline.frontier_ns if time_ns is None else time_ns,
            boundary=self.timeline.cursor,
            ordinal=self._ordinal if ordinal is None else ordinal,
        ).to_wire()

    @property
    def receipt_index(self) -> str:
        """Request ID of the command the owner is applying now."""
        if self._current is None:
            raise ContractError('no owner operation is active')
        return self._current.request_id

    def _emit(
        self,
        kind: str,
        future: Optional[Future[Any]] = None,
        *,
        position: Optional[WirePosition] = None,
        **values: Any,
    ) -> None:
        with self._lock:
            self._ordinal += 1
            published = dict(position or self._position(), ordinal=str(self._ordinal))
            self.channels.send_output(dict(kind=kind, position=published, **values), future)

    def bind_ready(
        self,
        *,
        operation: Operation,
        sink: Sink,
        cancel_queries: Optional[Callable[[], None]] = None,
    ) -> None:
        if self._state != 'ready' or self._pending:
            raise ContractError('bind experiment only before use')
        self.operation = operation
        self.channels.bind_sink(sink)
        if cancel_queries is not None:
            self._cancel_queries = cancel_queries

    def bind_lifecycle(self, lifecycle: Lifecycle) -> None:
        if self.lifecycle is not None or self._state != 'ready':
            raise ContractError('lifecycle already bound or started')
        self.lifecycle = lifecycle
        lifecycle.notice = lambda event, details: self._emit('notice', event=event, details=details)

    def publish_notice(self, event: str, details: Dict[str, Any]) -> bool:
        """Queue a notice; False once the output stream has ended."""
        with self._lock:
            if self._stream_sealed:
                return False
            self._emit('notice', event=event, details=details)
            return True

    def on_close(self, callback: Callable[[], None]) -> None:
        self._on_close = callback

    def require_owner(self) -> None:
        if (
            self._thread is None
            or get_ident() != self._thread.ident
            or self._state not in ('running', 'pausing')
        ):
            raise ContractError('operation requires the financial owner')

    def submit(self, payload: Any) -> Future[Any]:
        """Admit a command for the owner; the Future resolves when its result is delivered."""
        payload = copy_json(payload)
        with self._lock:
            if self._state != 'running':
                raise ContractError('experiment is not accepting commands')
            self.channels.reserve()
            identity = None
            try:
                identity = str(self._receipt_number + 1)
                command = self.sequencer.admit(identity, payload)
                future = running_future()
                self._pending[identity] = future
                self._receipt_number += 1
                prefix = self.reader.prefix(command.time_ns)
                self.publish_notice(
                    'command_admitted',
                    dict(
                        command_id=identity,
                        admission_sequence=str(command.sequence),
                        effective_ns=str(command.time_ns),
                        prepared_prefix=str(prefix),
                        source_sequence=str(self.reader.source_sequence(prefix)),
                        payload=payload,
                    ),
                )
            except Exception:
                if identity in self._pending:
                    # Admission already took effect. Fail closed and let the
                    # terminal drain release its slot exactly once.
                    self.sequencer.stop('failed')
                    self._wake.set()
                else:
                    self.channels.release()
                raise
        self._wake.set()
        return future

    def bind_market_reads(self, operation: ReadOperation) -> None:
        if self._state != 'ready' or self._read_operation is not None:
            raise ContractError('bind market reads only before experiment starts')
        self._read_operation = operation

    def submit_read(self, request: Any) -> Future[Any]:
        """Capture immutable market data at receipt without acquiring the financial owner.

        Only bounded projection is performed here; Tools process formats results.
        Account/state queries still use the ordered execution barrier.
        """
        with self._lock:
            if self._state != 'running' or self._read_operation is None:
                raise ContractError('market read capability is not active')
            self.channels.reserve()
            try:
                cutoff = self.clock.target_ns()
                if cutoff > self.reader.manifest['last_ns']:
                    raise ContractError('query exceeds prepared source coverage')
                prefix = self.reader.prefix(cutoff)
                position = dict(
                    sequence=str(self.reader.market_sequence(prefix)),
                    time_ns=str(cutoff),
                    boundary=str(prefix),
                    ordinal='0',
                )
                # Register only after capture succeeds. Otherwise the failure path
                # releases a slot while leaving a pending future for terminal drain.
                self._receipt_number += 1
                identity = str(self._receipt_number)
                future = running_future()
                self._pending[identity] = future
            except Exception:
                self.channels.release()
                raise
        try:
            result = self._read_operation(request, position, identity)
            with self._lock:
                if identity in self._pending:
                    self._emit(
                        'result',
                        future,
                        position=position,
                        request=request,
                        result=result,
                        receipt_target_ns=str(cutoff),
                    )
                    self._pending.pop(identity)
        except Exception as exc:
            _log.warning('Handled failure in submit_read', exc_info=True)
            with self._lock:
                if self._pending.pop(identity, None) is not None:
                    self.channels.release()
                    future.set_exception(exc)
        return future

    def _advance(self, index: int, time_ns: int) -> MarketEvent:
        actual = self.driver.pump()[0]
        # Background experiments always run on a prepared source.
        market = cast('PreparedSession', self.driver.market)
        if market.merged_prefix != index + 1 or actual.time_ns != time_ns:
            raise ContractError('prepared cursor and execution timeline differ')
        self.metrics.frame(self.clock.target_ns(), actual.time_ns)
        return actual

    def _advance_span(self, index: int, limit: int) -> int:
        """Advance a run of idle events at once when the reader and driver support it."""
        if not hasattr(self.reader, 'idle_span_end') or not hasattr(
            self.driver, 'advance_idle_prefix'
        ):
            return 0
        end = min(limit, self.reader.idle_span_end(index))
        if end - index < MIN_IDLE_SPAN:
            return 0
        count, event = self.driver.advance_idle_prefix(end)
        if count:
            self.metrics.span(self.clock.target_ns(), self.reader.event_time(index), count)
        return count

    def _observe_financial(self, position: WirePosition) -> None:
        if self.lifecycle is None:
            return
        version = self.driver.account_sequence
        if version != self._account_version:
            self.lifecycle.observe(position)
            self._account_version = version

    def _apply(self, command: Command) -> CommandResult:
        self._current = command
        started = perf_counter_ns()
        try:
            position = self._position(command.time_ns, self._ordinal + 1)
            account_before = getattr(self.driver, 'account_sequence', None)
            result = self.operation(command.payload, position)
            self._observe_financial(position)
            self.publish_notice(
                'command_processed',
                dict(
                    command_id=command.request_id,
                    effective_ns=str(command.time_ns),
                    admission_sequence=str(command.sequence),
                    financial_effect=(
                        (getattr(self.driver, 'account_sequence', None) != account_before)
                        if account_before is not None
                        else None
                    ),
                ),
            )
            future = self._pending[command.request_id]
            self._emit(
                'result',
                future,
                position=position,
                request=command.payload,
                result=result,
                receipt_target_ns=str(command.time_ns),
            )
            self._pending.pop(command.request_id)
            self.metrics.operation(perf_counter_ns() - started)
            return dict(status='processed')
        finally:
            self._current = None

    def _finish(self, reason: str, stamp: int) -> None:
        status = 'completed' if reason == 'end' else 'failed' if reason == 'failed' else 'stopped'
        self._terminate(status, reason, stamp)

    def _record_error(self, error: object) -> None:
        """Retain the initiating failure and separately expose later cleanup errors."""
        if self._error is None:
            self._error = str(error)
        else:
            self._cleanup_errors.append(str(error))

    def _cancel_requests(self, reason: str) -> None:
        for identity, future in list(self._pending.items()):
            if future.done():
                continue
            try:
                self.publish_notice('command_cancelled', dict(command_id=identity, reason=reason))
            except Exception as exc:
                _log.warning('Handled failure in _cancel_requests', exc_info=True)
                self._record_error(exc)
            future.set_exception(ContractError('experiment ended before operation'))
            self.channels.release()
        self._pending.clear()
        while True:
            try:
                _, control_future = self._controls.get_nowait()
            except Empty:
                break
            if control_future is not None and not control_future.done():
                control_future.set_exception(
                    ContractError('experiment ended before control acknowledgement')
                )

    def _stop_participants(self, reason: str) -> None:
        try:
            self._cancel_queries()
        except Exception as exc:
            _log.warning('Handled failure in _stop_participants', exc_info=True)
            self._record_error(exc)
        if self.lifecycle is not None:
            try:
                self.lifecycle.end(reason)
            except Exception as exc:
                _log.warning('Handled failure in _stop_participants', exc_info=True)
                self._record_error(exc)
        self._cancel_requests(reason)

    def _finish_financial(self, status: str, reason: str, stamp: int) -> None:
        try:
            self.driver.finish('failed' if self._error else status, reason, stamp)
        except Exception as exc:
            _log.warning('Handled failure in _finish_financial', exc_info=True)
            self._record_error(exc)
        try:
            self.clock.pause()
        except Exception as exc:
            _log.warning('Handled failure in _finish_financial', exc_info=True)
            self._record_error(exc)
        if hasattr(self.driver, 'execution'):
            try:
                from dataclasses import asdict

                self.publish_notice(
                    'prepared_final',
                    dict(
                        financial=asdict(self.driver.execution.snapshot),
                        position=self._position(),
                        **self._terminal_evidence(),
                    ),
                )
            except Exception as exc:
                _log.warning('Handled failure in _finish_financial', exc_info=True)
                self._record_error(exc)

    def _terminate(self, status: str, reason: str, stamp: Optional[int] = None) -> None:
        if self._state in ('completed', 'stopped', 'failed', 'ending'):
            return
        # Terminal state becomes visible only after the financial close attempt.
        # Output acknowledgement remains separate: channels.ended confirms drain.
        self._transition('ending')
        self._error = reason if status == 'failed' else None
        self.channels.cancel.set()
        self.channels.delivery.set()
        self._stop_participants(reason)
        self._finish_financial(
            status, reason, self.timeline.frontier_ns if stamp is None else stamp
        )
        with self._lock:
            self._transition('failed' if self._error else status)
            self._emit(
                'end',
                self.channels.ended,
                status=self._state,
                reason=self._error or reason,
                evaluation=self.metrics.snapshot(self._state),
            )
            self._stream_sealed = True

    def play(self) -> None:
        if self._state != 'ready':
            raise ContractError('experiment already started')
        if self._replay_seed is not None:
            self.publish_notice('prepared_run', self._replay_seed)
        self.driver.play()
        self.clock.resume()
        self._transition('running')

    def start(self) -> None:
        self.play()
        self._thread = Thread(target=self._run, name='execution-owner', daemon=True)
        self._thread.start()

    def wake(self) -> None:
        self._wake.set()

    def control(self, command: str) -> Future[Any]:
        """Queue 'pause', 'resume' or 'stop'; the Future resolves when the owner applies it."""
        if command not in ('pause', 'resume', 'stop'):
            raise ContractError('unknown experiment control')
        with self._lock:
            if self._state in ('completed', 'stopped', 'failed'):
                return self.channels.ended
            result: Optional[Future[Any]] = running_future()
            if command == 'stop':
                self.sequencer.stop()
                result = None
            elif command == 'pause':
                self.sequencer.pause(self.clock.pause)
                self._transition('pausing')
            self._controls.put_nowait((command, result))
        self._wake.set()
        # result is None only for 'stop', which returns the end Future instead.
        return self.channels.ended if command == 'stop' else cast('Future[Any]', result)

    def control_and_wait(self, command: str, timeout: float = CONTROL_ACK_TIMEOUT_SECONDS) -> Any:
        return self.control(command).result(timeout)

    def _service_control(self) -> None:
        try:
            command, future = self._controls.get_nowait()
        except Empty:
            return
        try:
            if command == 'stop':
                self.timeline.turn()
            elif command == 'pause':
                self.timeline.turn()  # Atomically sealed <=pause cutoff, including same-tick commands.
                if self._state in ('completed', 'stopped', 'failed'):
                    raise ContractError('experiment ended before pause acknowledgement')
                self.driver.pause()
                self._transition('paused')
            else:
                self.driver.play()
                self.sequencer.resume(self.clock.resume)
                self._transition('running')
                self.channels.delivery.set()
            result = dict(
                event=command,
                applied=True,
                participant='execution',
                phase='quiescent' if command == 'pause' else 'running',
                position=self._position(),
                state=self._state,
            )
            self._emit('control', event=command, applied=True)
            if future is not None:
                future.set_result(result)
        except Exception as exc:
            if future is not None:
                future.set_exception(exc)
            raise

    def tick(self) -> Dict[str, Any]:
        """One owner turn: apply controls and due work; any error ends the run as failed."""
        self.metrics.turns += 1
        if self._thread is not None and get_ident() != self._thread.ident:
            raise ContractError('execution owner exclusively drives experiment')
        try:
            self._service_control()
            if self.channels.output_error is not None:
                raise ContractError('experiment result delivery failed')
            if self._state == 'running':
                self.timeline.turn()
                if self.lifecycle is not None:
                    self.lifecycle.step(self._position())
                lag = min(self.clock.target_ns(), self.end_ns) - self.timeline.frontier_ns
                if lag > self.lag_limit_ns:
                    if self._lag_since is None:
                        self._lag_since = self.clock.target_ns()
                    if self.clock.target_ns() - self._lag_since >= self.lag_window_ns:
                        raise ContractError('execution frontier lag limit exceeded')
                else:
                    self._lag_since = None
        except Exception as exc:
            _log.warning('Handled failure in tick', exc_info=True)
            self.sequencer.stop('failed')
            self._terminate('failed', str(exc))
        return self.snapshot()

    def _run(self) -> None:
        while self._state not in ('completed', 'stopped', 'failed'):
            self._wake.clear()
            self.tick()
            if self._state in ('completed', 'stopped', 'failed'):
                break
            next_ns = (
                self.reader.event_time(self.timeline.cursor)
                if self.timeline.cursor < self.reader.manifest['frames']
                else self.end_ns
            )
            delay = min(
                MAX_IDLE_WAIT_SECONDS,
                real_delay_ns(self.clock, min(next_ns, self.end_ns)) / NS_PER_SECOND,
            )
            if self.sequencer.snapshot()['pending']:
                delay = 0
            self._wake.wait(delay if self._state == 'running' else None)

    def snapshot(self) -> Dict[str, Any]:
        try:
            target = min(self.clock.target_ns(), self.end_ns)
        except ContractError:
            # A Runtime killed while holding the shared clock's lock leaves the clock
            # unreadable; the owner then ends the run as failed. Report the executed
            # position instead, so status reads and close() still work.
            _log.warning('Experiment clock unreadable; reporting the executed position')
            target = self.timeline.frontier_ns
        return dict(
            state=self._state,
            target_ns=target,
            executed_ns=self.timeline.frontier_ns,
            lag_ns=max(0, target - self.timeline.frontier_ns),
            position=self._position(),
            error=self._error,
            cleanup_errors=list(self._cleanup_errors),
            evaluation=self.metrics.snapshot(self._state),
            output_error=str(self.channels.output_error) if self.channels.output_error else None,
        )

    @property
    def quiescent(self) -> bool:
        return (
            self._thread is None or not self._thread.is_alive()
        ) and not self.channels.output_thread.is_alive()

    def close(self) -> None:
        if self._state not in ('completed', 'stopped', 'failed'):
            future = self.control('stop')
            if self._thread is None:
                self.tick()
            future.result(CONTROL_ACK_TIMEOUT_SECONDS)
        if self._thread is not None:
            self._thread.join(EXECUTION_DRAIN_TIMEOUT_SECONDS)
            if self._thread.is_alive():
                raise ContractError('execution owner did not stop; ownership retained')
        self.channels.output_thread.join(EXECUTION_DRAIN_TIMEOUT_SECONDS)
        if self.channels.output_thread.is_alive():
            raise ContractError('result delivery did not drain; ownership retained')
        self._on_close()
        if self.channels.output_error is not None:
            raise ContractError('experiment output failed') from self.channels.output_error
