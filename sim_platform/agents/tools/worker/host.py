"""One persistent, trusted tools process per run; owner never waits on pipe IO.

Only sender/receiver adapters encode/decode and block on IPC. cancel_pending is
nonblocking; close reaps the child off-owner and retains resources on failure.
No database, market or execution capability is passed to the child.
"""

from __future__ import annotations
import logging

from concurrent.futures import Future
import json
import multiprocessing
from queue import Queue, Empty
from threading import Thread, RLock, Event
import time
from typing import TYPE_CHECKING, Any, Callable, Dict, Optional, Tuple, Union

from sim_platform.domain.models import ContractError
from sim_platform.domain.json_values import copy_json
from sim_platform.experiments.execution.results import DeferredResult
from sim_platform.agents.tools.reads.capture import CapturedRead
from sim_platform.agents.tools.trading.financial import format_financial
from sim_platform.agents.tools.reads.formatter import ReadFormatter
from sim_platform.agents.tools.protocol.responses import response_size, error_response
from sim_platform.agents.tools.protocol.contracts import ToolError
from sim_platform.agents.tools.protocol.transport import encode_job

from .process import run_tools

_log = logging.getLogger(__name__)

if TYPE_CHECKING:  # Annotation only.
    from sim_platform.agents.tools.trading.financial import FinancialCapture

    Captured = Union[CapturedRead, FinancialCapture]

# Child startup poll, IPC poll, and close() join waits (seconds).
READY_POLL_SECONDS = 0.02
IPC_POLL_SECONDS = 0.05
CHILD_JOIN_SECONDS = 0.2
FORCED_JOIN_SECONDS = 1
THREAD_JOIN_SECONDS = 1
# Extra bytes allowed around a response for its envelope, and longest error text.
ENVELOPE_BYTES = 1024
ERROR_TEXT_LIMIT = 256

TOOL_BINDING_FIELDS = (
    'run_id',
    'work_id',
    'epoch',
    'symbol',
    'start_ns',
    'end_ns',
    'source_manifest_ref',
    'execution_config_ref',
    'limits',
    'granted_tools',
)


class ToolProcessHost:
    """Bounded capture → process → Future adapter; caller retains ordered output slots.

    AgentTools creates one per run; it starts the tools child and its send/receive
    threads. close() stops and joins them, keeping ownership (raising) if they do
    not end.
    """

    def __init__(
        self,
        binding: Dict[str, Any],
        *,
        formatter_factory: Callable[[Dict[str, Any]], ReadFormatter] = ReadFormatter,
        startup_timeout: float = 5,
    ) -> None:
        self.binding = copy_json({name: binding[name] for name in TOOL_BINDING_FIELDS})
        self._queue: Queue[Tuple[int, Captured]] = Queue(binding['limits']['pending_requests'])
        self._pending: Dict[int, Tuple[Captured, Future[Any]]] = {}
        self._held: Dict[int, Tuple[Any, Optional[BaseException]]] = {}
        self._lock = RLock()
        self._closed = False
        self._ending = False
        self._error: Optional[str] = None
        self._identity = 0
        self._delivery = Event()
        self._delivery.set()
        context = multiprocessing.get_context('spawn')
        self._ready, self._child_stop = context.Event(), context.RawValue('b', 0)
        self._stop = Event()
        child_read, self._write = context.Pipe(duplex=False)
        self._read, child_write = context.Pipe(duplex=False)
        self._process = context.Process(
            target=run_tools,
            name='agent-tools',
            args=(
                child_read,
                child_write,
                self._ready,
                self._child_stop,
                self.binding,
                formatter_factory,
            ),
        )
        self._threads = [
            Thread(target=self._send, name='tools-ipc-send'),
            Thread(target=self._receive, name='tools-ipc-receive'),
        ]
        try:
            started = time.monotonic()
            self._process.start()
            self._pid = self._process.pid
            child_read.close()
            child_write.close()
            while not self._ready.wait(READY_POLL_SECONDS):
                if not self._process.is_alive() or time.monotonic() - started > startup_timeout:
                    raise ContractError('tools process did not become ready')
            for thread in self._threads:
                thread.start()
        except Exception:
            child_read.close()
            child_write.close()
            self.close()
            raise

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return dict(
                pid=getattr(self, '_pid', None),
                alive=self._process.is_alive() if not self._closed else False,
                pending=len(self._pending),
                error=self._error,
                state=(
                    'closed'
                    if self._closed
                    else ('failed' if self._error else ('ending' if self._ending else 'ready'))
                ),
            )

    def require_available(self) -> None:
        """Owner pre-effect check; no financial action starts after known transport failure."""
        if self._ending or self._error is not None or not self._process.is_alive():
            raise ToolError('TOOL_UNAVAILABLE', 'tools process is unavailable')
        if len(self._pending) >= self._queue.maxsize:
            raise ToolError('QUEUE_FULL', 'tools process capacity exhausted')

    def submit(self, captured: Captured) -> DeferredResult:
        """Owner enqueues references only: no encoding, pipe send or Future wait."""
        with self._lock:
            try:
                self.require_available()
            except ToolError:
                if not isinstance(captured, CapturedRead):
                    from sim_platform.agents.tools.trading.financial import financial_fallback

                    return financial_fallback(captured, self.binding)
                raise
            if len(self._pending) >= self._queue.maxsize:
                raise ToolError('QUEUE_FULL', 'tools process capacity exhausted')
            self._identity += 1
            identity = self._identity
            future: Future[Any] = Future()
            self._pending[identity] = captured, future
            self._queue.put_nowait((identity, captured))
        return DeferredResult(future)

    def _finish(
        self, identity: int, result: Any = None, error: Optional[BaseException] = None
    ) -> None:
        # Completion and cancellation share the same publication lock. A result
        # cannot disappear from pending just before Stop cancels unfinished reads.
        with self._lock:
            record = self._pending.get(identity)
            if record is None:
                return
            if not self._delivery.is_set() and not self._ending:
                self._held[identity] = result, error
                return
            self._pending.pop(identity)
            self._held.pop(identity, None)
            _, future = record
            if not future.done():
                if error is None:
                    future.set_result(result)
                else:
                    future.set_exception(error)

    def _flush_held(self) -> None:
        with self._lock:
            for identity, (result, error) in list(self._held.items()):
                self._finish(identity, result, error)

    def _send(self) -> None:
        try:
            while not self._stop.is_set():
                try:
                    identity, captured = self._queue.get(timeout=IPC_POLL_SECONDS)
                except Empty:
                    continue
                with self._lock:
                    if identity not in self._pending:
                        continue
                try:
                    self._write.send_bytes(encode_job(identity, captured))
                except ToolError as error:
                    self._finish(
                        identity,
                        error_response(
                            captured.request, captured.receipt_index, dict(captured.position), error
                        ),
                    )
                except (OSError, EOFError):
                    raise
                except Exception as error:
                    _log.warning('Handled failure in _send', exc_info=True)
                    self._finish(identity, error=error)
        except Exception as error:
            _log.warning('Handled failure in _send', exc_info=True)
            self._failed(str(error))

    def _receive(self) -> None:
        try:
            maximum = self.binding['limits']['tool_response_bytes'] + ENVELOPE_BYTES
            while not self._stop.is_set():
                if self._ending:
                    self._drain_financial()
                if not self._read.poll(IPC_POLL_SECONDS):
                    if not self._process.is_alive():
                        raise ContractError('tools process exited')
                    continue
                message = json.loads(self._read.recv_bytes(maximum))
                if (
                    set(message) != {'identity', 'result', 'fault'}
                    or type(message['identity']) is not int
                    or not 1 <= message['identity'] <= self._identity
                ):
                    raise ContractError('invalid tools response envelope')
                while not self._delivery.wait(IPC_POLL_SECONDS):
                    if self._stop.is_set():
                        return
                fault = message['fault']
                self._finish(
                    message['identity'], message['result'], RuntimeError(fault) if fault else None
                )
        except Exception as error:
            _log.warning('Handled failure in _receive', exc_info=True)
            if not self._stop.is_set():
                self._failed(str(error))

    def _finish_financial(self, identity: int, captured: FinancialCapture) -> None:
        """Fault/terminal-only formatting off-owner; retain truthful committed output."""
        try:
            result = format_financial(captured, self.binding['source_manifest_ref'])
            response_size(result, self.binding['limits']['tool_response_bytes'])
            self._finish(identity, result)
        except Exception as error:
            _log.warning('Handled failure in _finish_financial', exc_info=True)
            self._finish(identity, error=error)

    def _drain_financial(self) -> None:
        with self._lock:
            records = list(self._pending.items())
        for identity, (captured, _) in records:
            if not isinstance(captured, CapturedRead):
                self._finish_financial(identity, captured)

    def _failed(self, reason: str) -> None:
        with self._lock:
            if self._error is None:
                self._error = reason[:ERROR_TEXT_LIMIT]
            records = list(self._pending.items())
        for identity, (captured, _) in records:
            if isinstance(captured, CapturedRead):
                self._finish(
                    identity,
                    error_response(
                        captured.request,
                        captured.receipt_index,
                        dict(captured.position),
                        ToolError('TOOL_UNAVAILABLE', 'tools process exited'),
                    ),
                )
            else:
                # Output-only recovery of a retained outcome, never a resubmission.
                self._finish_financial(identity, captured)
        self._stop.set()
        self._child_stop.value = 1

    def pause(self) -> Dict[str, Any]:
        with self._lock:
            self._delivery.clear()
            return dict(participant='tools', phase='gate_closed', pid=self._process.pid)

    def play(self) -> Dict[str, Any]:
        with self._lock:
            self._delivery.set()
            self._flush_held()
            return dict(participant='tools', phase='gate_open', pid=self._process.pid)

    def cancel_pending(self) -> None:
        """Owner terminal hook: cancel pure reads only, never join/kill/wait here."""
        with self._lock:
            self._ending = True
            for identity, (captured, future) in list(self._pending.items()):
                if isinstance(captured, CapturedRead):
                    future.cancel()
                    self._pending.pop(identity)
                    self._held.pop(identity, None)
        self._delivery.set()  # Already committed financial output must drain.
        self._flush_held()

    def close(self) -> None:
        """Called after pipeline output drains; terminate stuck work without orphaning it."""
        if self._closed:
            return
        self.cancel_pending()
        self._stop.set()
        self._child_stop.value = 1
        self._delivery.set()
        if self._process.pid is not None:
            self._process.join(CHILD_JOIN_SECONDS)
            if self._process.is_alive():
                self._process.terminate()
                self._process.join(FORCED_JOIN_SECONDS)
            if self._process.is_alive():
                self._process.kill()
                self._process.join(FORCED_JOIN_SECONDS)
            if self._process.is_alive():
                raise ContractError('tools process did not exit')
        for thread in self._threads:
            if thread.ident is not None:
                thread.join(THREAD_JOIN_SECONDS)
        if any(thread.is_alive() for thread in self._threads):
            raise ContractError('tools IPC adapters did not close')
        self._write.close()
        self._read.close()
        with self._lock:
            self._process.close()
            self._closed = True
            self._pending.clear()
            self._held.clear()
            while not self._queue.empty():
                self._queue.get_nowait()
