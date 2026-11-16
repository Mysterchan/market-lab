"""A deferred read response; its Future contains detached JSON, never live state."""

from __future__ import annotations

from dataclasses import dataclass
from concurrent.futures import Future
from typing import Any


@dataclass(frozen=True)
class DeferredResult:
    """A result that is not ready yet; the output stage waits for ``future``.

    The scheduling policy creates one for a pending work subscription; the output
    thread resolves it before delivering the message that carries it.
    """

    future: Future[Any]

    def resolve(self) -> Any:
        return self.future.result()
