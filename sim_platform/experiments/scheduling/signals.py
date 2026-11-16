"""Trusted process flags without a mutex that a killed child can strand.

Finite polling is limited to lifecycle gates, never market/work scheduling.
One parent writes activation/stop; ready is one child-written setup signal.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:  # Annotation only.
    from multiprocessing.context import BaseContext

# Sleep between checks while waiting for a signal.
POLL_SECONDS = 0.005


class ProcessSignal:
    """A set/clear flag in shared memory, readable from spawned children.

    The Agent host creates its lifecycle signals with the spawn context and passes
    them to its children; the flag lives as long as any process holds it.
    """

    def __init__(self, context: BaseContext) -> None:
        self._value = context.RawValue('b', 0)

    def is_set(self) -> bool:
        return bool(self._value.value)

    def set(self) -> None:
        self._value.value = 1

    def clear(self) -> None:
        self._value.value = 0

    def wait(self, timeout: Optional[float] = None) -> bool:
        """Poll until set; False if ``timeout`` seconds pass first."""
        deadline = None if timeout is None else time.monotonic() + timeout
        while not self.is_set():
            if deadline is not None and time.monotonic() >= deadline:
                return False
            time.sleep(
                POLL_SECONDS
                if deadline is None
                else min(POLL_SECONDS, max(0, deadline - time.monotonic()))
            )
        return True
