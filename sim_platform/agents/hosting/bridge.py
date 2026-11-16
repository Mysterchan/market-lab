"""App-side Agent IPC bridge: receive requests and deliver detached results."""

from __future__ import annotations
import logging

from concurrent.futures import Future
from queue import Queue, Empty, Full
from threading import Event, Thread, RLock, BoundedSemaphore
import time
from typing import TYPE_CHECKING, Any, Callable, Dict, Optional, Set, Tuple, cast

from sim_platform.agents.configuration import validate_identifier
from sim_platform.domain.models import ContractError


from .protocol import packet, unpack

_log = logging.getLogger(__name__)

if TYPE_CHECKING:  # Annotation only.
    from multiprocessing.connection import Connection

    from .protocol import TransportLimits

# How long each wait lasts before Stop is checked again.
POLL_SECONDS = 0.05
# Bytes allowed around a request/response for its envelope; longest error text.
ENVELOPE_BYTES = 512
ERROR_TEXT_LIMIT = 256
THREAD_JOIN_SECONDS = 1


class ToolPipeBridge:
    """App-side transport borrowing the runner's tool and optional work callbacks.

    Ingress owns frame checks, rate limiting and request admission. The runner
    owns tool authorization and execution. Egress alone writes completed replies.
    A capacity slot stays held until its reply is written, including blocked writes.
    The Agent host creates one per Agent process and closes it after the child is reaped.
    """

    def __init__(
        self,
        request_tool: Callable[[Dict[str, Any]], Future[Any]],
        incoming: Connection,
        outgoing: Connection,
        limits: TransportLimits,
        *,
        request_work: Optional[Callable[[], Future[Any]]] = None,
    ) -> None:
        self._request_tool = request_tool
        self._request_work = request_work
        self._incoming, self._outgoing, self._limits = incoming, outgoing, limits
        self._output: Queue[Dict[str, Any]] = Queue(limits.pending)
        self._slots = BoundedSemaphore(limits.pending)
        self._pending: Set[str] = set()
        self._sending = False
        self._lock = RLock()
        self._stop = Event()
        self._error: Optional[str] = None
        self._received = self._delivered = 0
        self._threads = [
            Thread(target=self._receive, name='agent-ipc-ingress'),
            Thread(target=self._send, name='agent-ipc-egress'),
        ]

    def start(self) -> None:
        for thread in self._threads:
            thread.start()

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return dict(
                error=self._error,
                pending=len(self._pending) + int(self._sending),
                received=self._received,
                delivered=self._delivered,
            )

    def disconnect(self, error: Optional[object] = None) -> None:
        with self._lock:
            if error is not None and self._error is None:
                self._error = str(error)
            self._stop.set()

    def _enqueue(self, identity: str, result: Any = None, error: Optional[str] = None) -> None:
        # Called from a pipeline output Future: never encode, send or wait here.
        with self._lock:
            if self._stop.is_set():
                return
            try:
                self._output.put_nowait(dict(request_id=identity, result=result, error=error))
            except Full:
                self.disconnect('IPC output capacity invariant violated')

    def _complete(self, identity: str, future: Future[Any]) -> None:
        try:
            self._enqueue(identity, result=future.result()['result'])
        except Exception as exc:
            _log.warning('Handled failure in _complete', exc_info=True)
            self._enqueue(identity, error=str(exc)[:ERROR_TEXT_LIMIT])

    def _decode_request(self, raw: bytes) -> Tuple[str, Optional[Dict[str, Any]]]:
        """Check the transport envelope; return an ID and tool request (None for work).

        This checks neither tool grants nor financial arguments. Those checks
        remain at the runner's admission boundary after a valid frame is read.
        """
        message = unpack(raw)
        if set(message) == {'request_id', 'command'}:
            if message['command'] != 'next_work' or self._request_work is None:
                raise ContractError('unsupported IPC work capability')
            identity, request = message['request_id'], None
        elif set(message) == {'request'}:
            request = message['request']
            if type(request) is not dict:
                raise ContractError('tool request must be an object')
            packet(request, self._limits.request_bytes)
            identity = request.get('request_id')
        else:
            raise ContractError('invalid IPC request envelope')
        validate_identifier(identity)
        return identity, request

    def _register_request(self, identity: str) -> None:
        """Reject unfinished duplicates and account for the acquired capacity slot."""
        with self._lock:
            if identity in self._pending:
                raise ContractError('duplicate in-flight IPC identity')
            self._pending.add(identity)
            self._received += 1

    def _dispatch_request(self, identity: str, request: Optional[Dict[str, Any]]) -> None:
        """Submit once; route either immediate admission errors or Future results to egress."""
        try:
            # A work request (None) was only accepted when request_work is bound.
            future = (
                cast(Callable[[], Future[Any]], self._request_work)()
                if request is None
                else self._request_tool(request)
            )
        except Exception as exc:
            _log.warning('Handled failure in _dispatch_request', exc_info=True)
            self._enqueue(identity, error=str(exc)[:ERROR_TEXT_LIMIT])
        else:
            future.add_done_callback(lambda source: self._complete(identity, source))

    def _receive(self) -> None:
        """Ingress thread: apply backpressure before reading any untrusted frame."""
        next_admission = 0.0
        try:
            while not self._stop.is_set():
                # Backpressure is applied before reading/decoding, including
                # invalid requests. Neither rate nor slot waiting touches owner.
                if self._stop.wait(max(0, next_admission - time.monotonic())):
                    return
                if not self._slots.acquire(timeout=POLL_SECONDS):
                    continue
                if not self._incoming.poll(POLL_SECONDS):
                    self._slots.release()
                    continue
                raw = self._incoming.recv_bytes(self._limits.request_bytes + ENVELOPE_BYTES)
                identity, request = self._decode_request(raw)
                self._register_request(identity)
                next_admission = time.monotonic() + 1 / self._limits.requests_per_second
                self._dispatch_request(identity, request)
        except (EOFError, BrokenPipeError):
            # Windows poll() can observe a closed named pipe before recv_bytes()
            # translates it to EOF. The supervisor classifies the process exit.
            self.disconnect()
        except (OSError, ContractError) as exc:
            self.disconnect(exc)
        except Exception as exc:
            _log.warning('Handled failure in _receive', exc_info=True)
            self.disconnect('IPC ingress failed: ' + str(exc))

    def _encode_reply(self, message: Dict[str, Any]) -> bytes:
        """Bound the reply frame, replacing oversized results with a transport error."""
        try:
            return packet(message, self._limits.response_bytes + ENVELOPE_BYTES)
        except ContractError:
            message.update(result=None, error='IPC response exceeds byte limit')
            return packet(message, self._limits.response_bytes + ENVELOPE_BYTES)

    def _send(self) -> None:
        try:
            while not self._stop.is_set():
                try:
                    message = self._output.get(timeout=POLL_SECONDS)
                except Empty:
                    continue
                raw = self._encode_reply(message)
                with self._lock:
                    # The peer can receive and reuse this identity before
                    # send_bytes returns. Retire it before exposing the reply;
                    # keep its capacity slot until the write has completed.
                    self._pending.remove(message['request_id'])
                    self._sending = True
                self._outgoing.send_bytes(raw)
                with self._lock:
                    self._sending = False
                    self._delivered += 1
                self._slots.release()
        except (EOFError, BrokenPipeError):
            self.disconnect()
        except OSError as exc:
            self.disconnect(exc)
        except Exception as exc:
            _log.warning('Handled failure in _send', exc_info=True)
            self.disconnect('IPC egress failed: ' + str(exc))

    def close(self) -> None:
        """Child must be reaped first, unblocking any pending framed send/read."""
        self.disconnect()
        for thread in self._threads:
            if thread.ident is not None:
                thread.join(THREAD_JOIN_SECONDS)
        if any(thread.is_alive() for thread in self._threads):
            raise ContractError('IPC bridge did not close')
        self._incoming.close()
        self._outgoing.close()
        with self._lock:
            self._pending.clear()
            self._sending = False
            while not self._output.empty():
                self._output.get_nowait()
