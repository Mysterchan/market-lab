"""Owned preparation jobs shared by interpreter and environment setup."""

from __future__ import annotations
import logging

from threading import Event, RLock, Thread
from typing import Any, Callable, Dict, Optional
from sim_platform.domain.json_values import copy_json
from sim_platform.domain.models import ContractError
from .errors import PreparationCancelled

_log = logging.getLogger(__name__)

# close() waits this long for the worker thread.
CLOSE_JOIN_SECONDS = 5


class PreparationJob:
    """Subclasses perform work; the caller polls, cancels, waits and closes the job.

    Platform registers each job as a root; close() cancels it and waits for its
    worker thread, keeping ownership (raising) if the thread does not stop.
    """

    def __init__(self, name: str) -> None:
        self._cancel = Event()
        self._lock = RLock()
        self._state: Dict[str, Any] = dict(
            state='preparing', phase='waiting', error=None, reused=False
        )
        self._on_close: Callable[[], None] = lambda: None
        self._thread = Thread(target=self._run, name=name)

    def start(self) -> None:
        self._thread.start()

    def on_close(self, callback: Callable[[], None]) -> None:
        self._on_close = callback

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return copy_json(self._state)

    def update(self, **values: Any) -> None:
        with self._lock:
            self._state.update(values)

    def phase(self, phase: str) -> None:
        self.update(phase=phase)

    def _run(self) -> None:
        try:
            self.perform()
        except PreparationCancelled:
            self.update(state='cancelled', phase='cancelled')
        except Exception as exc:
            _log.warning('Handled failure in _run', exc_info=True)
            self.update(
                state='failed',
                phase='failed',
                error=dict(code=getattr(exc, 'code', 'PREPARATION_FAILED'), message=str(exc)),
            )

    def perform(self) -> None:
        raise NotImplementedError

    def cancel(self) -> None:
        self._cancel.set()

    def wait(self, timeout: Optional[float] = None) -> Dict[str, Any]:
        self._thread.join(timeout)
        return self.snapshot()

    def close(self) -> None:
        self.cancel()
        self._thread.join(CLOSE_JOIN_SECONDS)
        if self._thread.is_alive():
            raise ContractError(
                'preparation worker has not stopped; retain ownership for close retry'
            )
        self._on_close()
