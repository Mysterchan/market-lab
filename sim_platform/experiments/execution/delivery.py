"""Bounded ordered result/JSON delivery off the financial owner.

Slots are held from admission until delivery, including deferred Tools results.
A reserved terminal slot allows failure shutdown when ordinary capacity is full.
"""

from __future__ import annotations
import logging

from concurrent.futures import Future, CancelledError
from queue import Queue, Full
from threading import BoundedSemaphore, Event, RLock, Thread
from typing import Any, Callable, Dict, Optional, Tuple

from sim_platform.domain.models import ContractError
from sim_platform.domain.json_values import copy_json
from sim_platform.experiments.execution.results import DeferredResult

_log = logging.getLogger(__name__)

# Notices queued at once when no capacity is given.
DEFAULT_EVENT_CAPACITY = 128
# Extra notice slots beyond the event capacity, for lifecycle notices.
NOTICE_RESERVE = 16
# The output queue's reserved slot for the terminal 'end' message.
TERMINAL_SLOTS = 1

# An output message: 'kind' ('result', 'notice', 'control' or 'end'), 'position' and
# kind-specific values; a 'result' may carry a DeferredResult until it is delivered.
OutputMessage = Dict[str, Any]
# Receives each delivered message (a detached copy).
Sink = Callable[[OutputMessage], None]


def running_future() -> Future[Any]:
    """A Future already marked running, so it can no longer be cancelled."""
    result: Future[Any] = Future()
    result.set_running_or_notify_cancel()
    return result


class ResultDelivery:
    """The output thread that publishes results and notices in order, off the owner.

    The ExperimentSession creates one; it starts its output thread at once and the
    thread ends after delivering the 'end' message. ``slots`` bounds admitted
    requests until their result is delivered.

    Args:
        capacity: Most admitted requests awaiting delivery.
        sink: Receives each delivered message.
        wake: Set when output fails, to wake the execution owner.
        event_capacity: Most queued notices.
    """

    def __init__(
        self,
        capacity: int,
        sink: Sink,
        wake: Event,
        event_capacity: int = DEFAULT_EVENT_CAPACITY,
    ) -> None:
        self.sink, self.wake = sink, wake
        self.slots = BoundedSemaphore(capacity)
        self._notices = BoundedSemaphore(event_capacity + NOTICE_RESERVE)
        self.output: Queue[Tuple[OutputMessage, Optional[Future[Any]]]] = Queue(
            capacity + event_capacity + NOTICE_RESERVE + TERMINAL_SLOTS
        )
        self.delivery, self.cancel = Event(), Event()
        self.delivery.set()
        self.ended = running_future()
        self.output_error: Optional[BaseException] = None
        self._publication_lock = RLock()
        self._used = False
        self.output_thread = Thread(target=self._run, name='experiment-output', daemon=True)
        self.output_thread.start()

    def reserve(self) -> None:
        """Take a result slot for a new request, or raise if closed or full."""
        if self.cancel.is_set() or not self.slots.acquire(blocking=False):
            raise ContractError('experiment closed or result capacity exhausted')
        self._used = True

    def release(self) -> None:
        self.slots.release()

    def bind_sink(self, sink: Sink) -> None:
        if self._used or self.cancel.is_set():
            raise ContractError('result delivery already used')
        self.sink = sink

    def pause_delivery(self) -> None:
        with self._publication_lock:
            self.delivery.clear()

    def send_output(self, message: OutputMessage, future: Optional[Future[Any]] = None) -> None:
        """Queue a message without blocking; ``future`` is resolved when it is delivered."""
        if message['kind'] == 'notice' and not self._notices.acquire(blocking=False):
            raise ContractError('experiment notification capacity exhausted')
        try:
            self.output.put_nowait((message, future))
        except Full:
            if message['kind'] == 'notice':
                self._notices.release()
            raise ContractError('experiment output capacity exhausted') from None

    def _publish(self, message: OutputMessage, future: Optional[Future[Any]]) -> None:
        if self.output_error is not None:
            raise ContractError('experiment output failed') from self.output_error
        self.sink(copy_json(message))
        if future is not None and not future.done():
            future.set_result(copy_json(message))

    def _run(self) -> None:
        while True:
            message, future = self.output.get()
            try:
                if isinstance(message.get('result'), DeferredResult):
                    message = dict(message, result=message['result'].resolve())
                if message['kind'] == 'result':
                    while True:
                        self.delivery.wait()
                        with self._publication_lock:
                            if self.delivery.is_set():
                                self._publish(message, future)
                                break
                else:
                    self._publish(message, future)
            except CancelledError:
                if future is not None and not future.done():
                    future.set_exception(ContractError('experiment stopped before result'))
            except Exception as exc:
                _log.warning('Handled failure in _run', exc_info=True)
                self.output_error = exc
                if future is not None and not future.done():
                    future.set_exception(exc)
                self.wake.set()
            finally:
                if message['kind'] == 'notice':
                    self._notices.release()
                if future is not None and message['kind'] == 'result':
                    self.slots.release()
            if message['kind'] == 'end':
                return
