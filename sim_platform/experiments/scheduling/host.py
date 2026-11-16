"""Owned Runtime child with bounded nonblocking admission and detached replies.

Only sender/receiver threads wait on pipes; submit never blocks a market owner.
close is an off-owner operation, retains ownership if cleanup is unconfirmed.
The handle is infrastructure, not a second user-facing experiment API.
"""

from __future__ import annotations
import logging

from concurrent.futures import Future
import multiprocessing
from queue import Queue, Empty
from threading import Thread, RLock, Event
import time
from math import isfinite
from typing import TYPE_CHECKING, Any, Callable, Dict, Optional, Set

from sim_platform.domain.json_values import copy_json
from sim_platform.domain.models import ContractError, integer
from sim_platform.resources import cleanup_after_error
from sim_platform.experiments.scheduling.clock import SharedExperimentClock, StopSignal
from sim_platform.experiments.scheduling.protocol import (
    MAX_BYTES,
    packet,
    unpack,
    validate_payload,
    reply,
)
from sim_platform.experiments.scheduling.worker import run_runtime
from sim_platform.experiments.scheduling.client import RuntimeClient

_log = logging.getLogger(__name__)

if TYPE_CHECKING:  # Annotation only.
    from multiprocessing.connection import Connection
    from multiprocessing.process import BaseProcess

    from sim_platform.experiments.scheduling.protocol import Message
    from sim_platform.experiments.scheduling.worker import NoticeCallback, PolicyFactory

# Admission bounds: default and largest request capacity, and queued controls.
DEFAULT_CAPACITY = 64
MAX_CAPACITY = 1024
MAX_PENDING_CONTROLS = 4
# Child startup: default and longest wait for 'ready', and the poll interval.
DEFAULT_STARTUP_TIMEOUT_SECONDS = 5
MAX_STARTUP_TIMEOUT_SECONDS = 60
READY_POLL_SECONDS = 0.01
# Sender threads re-check the stop flag at this interval.
SEND_POLL_SECONDS = 0.05
# close(): wait per termination step for the child, and for each IO thread.
PROCESS_JOIN_SECONDS = 0.5
THREAD_JOIN_SECONDS = 1

# Receives a Runtime failure.
FailureCallback = Callable[[Exception], None]


class RuntimeError(ContractError):
    """A Runtime refusal with a stable ``code`` (for example 'QUEUE_FULL').

    Note: this name shadows the built-in RuntimeError inside this module.
    """

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class ExperimentRuntimeHandle:
    """The App side of one Runtime child process and its pipes.

    Created during experiment assembly; it starts the child, its sender/receiver
    threads, and waits for 'ready'. submit() never blocks; replies arrive on the
    receiver thread. close() stops and joins everything and raises (keeping
    ownership) if the child or a thread does not end.

    Args:
        factory: Builds the work policy inside the child.
        configuration: JSON policy configuration (must include 'start_ns').
        capacity: Most outstanding requests.
        startup_timeout: Seconds to wait for the child's 'ready'.
        clock: Shared clock; a new one starts at 'start_ns' if omitted.
        on_notice: Receives Runtime notices on the receiver thread.
        on_failure: Receives the first Runtime failure.
    """

    # Set in __init__; declared here because the IO methods are defined first.
    clock: SharedExperimentClock
    _lock: RLock
    _ready: Event
    _stop: StopSignal
    _local_stop: Event
    _queue: Queue[bytes]
    _control_queue: Queue[bytes]
    _control_ids: Set[str]
    _pending: Dict[str, Future[Any]]
    _number: int
    _capacity: int
    _view: Dict[str, Any]
    _error: Optional[Exception]
    _closed: bool
    _callbacks_bound: bool
    _on_notice: NoticeCallback
    _on_failure: FailureCallback
    _write: Connection
    _control_write: Connection
    _read: Connection
    _client_outgoing: Connection
    _client_incoming: Connection
    _client_borrowed: bool
    _process: BaseProcess
    _sender: Thread
    _control_sender: Thread
    _receiver: Thread

    def _fail(self, error: Exception, *, notify: bool = True) -> None:
        with self._lock:
            if self._error is not None:
                return
            self._error = error
            pending, self._pending = self._pending, {}
        self._local_stop.set()
        self._stop.set()
        self.clock.changed.release()
        for future in pending.values():
            if not future.done():
                future.set_exception(error)
        if notify:
            self._on_failure(error)

    def _send_control(self) -> None:
        self._send(self._control_queue, self._control_write)

    def _send(
        self, queue: Optional[Queue[bytes]] = None, outgoing: Optional[Connection] = None
    ) -> None:
        queue = self._queue if queue is None else queue
        outgoing = self._write if outgoing is None else outgoing
        try:
            while not self._local_stop.is_set():
                try:
                    raw = queue.get(timeout=SEND_POLL_SECONDS)
                except Empty:
                    continue
                outgoing.send_bytes(raw)
        except (OSError, EOFError):
            if not self._stop.is_set():
                self._fail(ContractError('runtime request channel failed'))

    def _message(self, message: Message) -> None:
        reply(message)
        kind = message.get('kind')
        if kind == 'ready':
            self._ready.set()
            return
        if kind == 'notice':
            self._on_notice(message['event'], copy_json(message['details']))
            return
        if kind not in ('snapshot', 'reply', 'ended'):
            raise ContractError('invalid runtime reply kind')
        with self._lock:
            self._view = copy_json(message['snapshot'])
            future = self._pending.pop(message['id'], None) if kind == 'reply' else None
            if kind == 'reply':
                self._control_ids.discard(message['id'])
        if kind == 'reply':
            if future is None:
                raise ContractError('unexpected runtime reply identity')
            if message['error'] is not None:
                error = message['error']
                future.set_exception(RuntimeError(error['code'], error['message']))
            else:
                future.set_result(copy_json(message['result']))

    def _receive(self) -> None:
        try:
            while True:
                self._message(unpack(self._read.recv_bytes(MAX_BYTES)))
        except (OSError, EOFError):
            self._fail(
                ContractError(
                    'runtime stopped' if self._stop.is_set() else 'runtime process disconnected'
                ),
                notify=not self._stop.is_set(),
            )
        except Exception as exc:
            _log.warning('Handled failure in _receive', exc_info=True)
            self._fail(exc)

    def request_stop(self) -> None:
        """Reserved shared stop signal, independent of a full command pipe."""
        self._stop.set()
        self._local_stop.set()
        self.clock.changed.release()

    def close(self) -> None:
        if self._closed:
            return
        self.request_stop()
        if self._process.pid is not None:
            self._process.join(PROCESS_JOIN_SECONDS)
            if self._process.is_alive():
                self._process.terminate()
                self._process.join(PROCESS_JOIN_SECONDS)
            if self._process.is_alive():
                self._process.kill()
                self._process.join(PROCESS_JOIN_SECONDS)
            if self._process.is_alive():
                raise ContractError('runtime process did not close; ownership retained')
        for thread in (self._sender, self._control_sender, self._receiver):
            if thread.ident is not None:
                thread.join(THREAD_JOIN_SECONDS)
                if thread.is_alive():
                    raise ContractError('runtime IO did not close; ownership retained')
        self._write.close()
        self._control_write.close()
        self._read.close()
        self._client_outgoing.close()
        self._client_incoming.close()
        self._closed = True

    def __init__(
        self,
        factory: PolicyFactory,
        configuration: Dict[str, Any],
        *,
        capacity: int = DEFAULT_CAPACITY,
        startup_timeout: float = DEFAULT_STARTUP_TIMEOUT_SECONDS,
        clock: Optional[SharedExperimentClock] = None,
        on_notice: Optional[NoticeCallback] = None,
        on_failure: Optional[FailureCallback] = None,
    ) -> None:
        integer(capacity, 'runtime capacity', 1)
        if capacity > MAX_CAPACITY:
            raise ContractError('runtime capacity exceeds supported bound')
        if not callable(factory):
            raise ContractError('runtime requires a trusted policy factory')
        if (
            type(startup_timeout) not in (int, float)
            or not isfinite(startup_timeout)
            or startup_timeout <= 0
            or startup_timeout > MAX_STARTUP_TIMEOUT_SECONDS
        ):
            raise ContractError('invalid runtime startup timeout')
        configuration = copy_json(configuration)
        packet(configuration)
        if clock is not None and not isinstance(clock, SharedExperimentClock):
            raise ContractError('runtime requires a shared experiment clock')
        context = multiprocessing.get_context('spawn')
        self.clock = clock or SharedExperimentClock(context, configuration['start_ns'])
        self._ready, self._stop = Event(), StopSignal(context)
        self._local_stop = Event()
        self._queue = Queue(capacity)
        self._control_queue = Queue(MAX_PENDING_CONTROLS)
        self._control_ids = set()
        self._lock = RLock()
        self._pending = {}
        self._number = 0
        self._capacity = capacity
        self._view = {}
        self._error = None
        self._closed = False
        self._callbacks_bound = on_notice is not None or on_failure is not None
        self._on_notice = on_notice or (lambda event, details: None)
        self._on_failure = on_failure or (lambda error: None)
        child_in, self._write = context.Pipe(duplex=False)
        control_in, self._control_write = context.Pipe(duplex=False)
        self._read, child_out = context.Pipe(duplex=False)
        client_in, self._client_outgoing = context.Pipe(duplex=False)
        self._client_incoming, client_out = context.Pipe(duplex=False)
        self._client_borrowed = False
        self._process = context.Process(
            target=run_runtime,
            name='experiment-runtime',
            args=(
                child_in,
                child_out,
                client_in,
                client_out,
                control_in,
                self._stop,
                self.clock.reader(),
                factory,
                configuration,
                capacity,
            ),
        )
        self._sender = Thread(target=self._send, name='runtime-ipc-send', daemon=True)
        self._control_sender = Thread(
            target=self._send_control, name='runtime-control-send', daemon=True
        )
        self._receiver = Thread(target=self._receive, name='runtime-ipc-receive', daemon=True)
        try:
            self._process.start()
            child_in.close()
            control_in.close()
            child_out.close()
            client_in.close()
            client_out.close()
            self._sender.start()
            self._control_sender.start()
            self._receiver.start()
            deadline = time.monotonic() + startup_timeout
            while not self._ready.wait(READY_POLL_SECONDS):
                if not self._process.is_alive() or time.monotonic() >= deadline:
                    raise ContractError('runtime process did not become ready')
        except Exception as exc:
            child_in.close()
            control_in.close()
            child_out.close()
            client_in.close()
            client_out.close()
            cleanup_after_error(exc, self.close)
            raise

    def bind_callbacks(self, *, on_notice: NoticeCallback, on_failure: FailureCallback) -> None:
        """Bind lifecycle observers once during assembly, before Play.

        Callbacks run on the Runtime receiver thread and must not wait for a
        Runtime reply. The handle retains transport/failure ownership.
        """
        with self._lock:
            if self._callbacks_bound or self._closed or self._pending:
                raise ContractError('bind Runtime observers once before use')
            self._on_notice = on_notice
            self._on_failure = on_failure
            self._callbacks_bound = True

    def work_client(self) -> RuntimeClient:
        """Setup-only single borrowed endpoint; caller must own its carrier host."""
        with self._lock:
            if self._client_borrowed or self._closed or self._stop.is_set():
                raise ContractError('work capability already borrowed or runtime closed')
            self._client_borrowed = True
            return RuntimeClient(
                self._client_outgoing, self._client_incoming, self._stop, self.clock.reader()
            )

    def submit(self, name: str, data: Optional[Dict[str, Any]] = None) -> Future[Any]:
        """Trusted adapter admission; never wait for child, clock or market progress."""
        data = copy_json({} if data is None else data)
        validate_payload(name, data)
        with self._lock:
            if self._closed or self._stop.is_set() or self._error is not None:
                raise ContractError('runtime is closed to new requests')
            control = name == 'control'
            if (control and len(self._control_ids) >= MAX_PENDING_CONTROLS) or (
                not control and len(self._pending) - len(self._control_ids) >= self._capacity
            ):
                raise RuntimeError('QUEUE_FULL', 'runtime capacity is full')
            self._number += 1
            identity = str(self._number)
            raw = packet(dict(id=identity, command=name, data=data))
            future: Future[Any] = Future()
            future.set_running_or_notify_cancel()
            self._pending[identity] = future
            try:
                if control:
                    self._control_ids.add(identity)
                (self._control_queue if control else self._queue).put_nowait(raw)
            except Exception:
                del self._pending[identity]
                self._control_ids.discard(identity)
                raise
            return future

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return dict(
                copy_json(self._view),
                pid=self._process.pid,
                alive=self._process.is_alive(),
                closed=self._closed,
                pending=len(self._pending),
                error=str(self._error) if self._error else None,
            )

    def __enter__(self) -> ExperimentRuntimeHandle:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
