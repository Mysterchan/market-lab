"""Cooperative background jobs. Cancellation never writes through a shared session."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from threading import Event
from typing import Any, Callable, Optional


class Cancellation:
    """A cancel flag shared between a job's owner and the job; set once, never cleared.

    BackgroundJob creates one per job and passes it to the operation as ``cancellation``.
    """

    def __init__(self) -> None:
        self._event = Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()


class BackgroundJob:
    """At most one independent job; its result is consumed by the owning UI worker.

    Platform creates it and registers it as a resource; the owning UI worker starts
    jobs and polls take_result(). close() cancels the job, waits for its thread and
    then runs the on_close callback once.
    """

    def __init__(self) -> None:
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='research-job')
        self.future: Optional[Future[Any]] = None
        self.token: Optional[Cancellation] = None
        self._closed = False
        self._on_close: Callable[[], None] = lambda: None

    def on_close(self, callback: Callable[[], None]) -> None:
        self._on_close = callback

    @property
    def active(self) -> bool:
        return self.future is not None

    def start(self, operation: Callable[..., Any], **arguments: Any) -> None:
        """Run ``operation(cancellation=..., **arguments)`` on the job thread."""
        if self._closed:
            raise ValueError('Research job is closed')
        if self.active:
            raise ValueError('A research job is already active')
        self.token = Cancellation()
        self.future = self.executor.submit(operation, cancellation=self.token, **arguments)

    def cancel(self) -> None:
        if self.token is not None:
            self.token.cancel()

    def take_result(self) -> Any:
        """Return the finished job's result (re-raising its error), or None while running."""
        if self.future is None or not self.future.done():
            return None
        future, self.future = self.future, None
        return future.result()

    def close(self) -> None:
        if not self._closed:
            try:
                self.cancel()
                self.executor.shutdown(wait=True, cancel_futures=True)
            finally:
                self._closed = True
                self._on_close()
