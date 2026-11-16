"""Cancellable cross-instance lease for publishing preparation artifacts."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import time
from typing import Callable, Iterator


from sim_platform.persistence.locking import acquire_exclusive, release_exclusive

# Wait between attempts to take a lease another process holds.
RETRY_SECONDS = 0.05


@contextmanager
def preparation_lease(root: Path, cancelled: Callable[[], bool]) -> Iterator[bool]:
    """Hold the folder's preparation lock; yields False if cancelled while waiting."""
    root.mkdir(parents=True, exist_ok=True)
    with (root / '.prepare.lock').open('ab') as stream:
        while True:
            if cancelled():
                yield False
                return
            if acquire_exclusive(stream, blocking=False):
                break
            time.sleep(RETRY_SECONDS)
        try:
            yield True
        finally:
            release_exclusive(stream)
