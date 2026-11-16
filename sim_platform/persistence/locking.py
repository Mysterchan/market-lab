"""Exclusive local-file locks shared by workspace and artifact owners.

Callers own the open binary stream and its lifetime. Closing it releases the lock,
including after process exit. Lock files must not be replaced while owners are active.
"""

from __future__ import annotations

import errno
import os
import time
from typing import Any, BinaryIO, cast

# Wait between attempts while blocking on a Windows lock.
RETRY_SECONDS = 0.05


def _try_windows_lock(stream: BinaryIO) -> bool:
    import msvcrt

    # CRT locks start at the current offset, unlike flock's whole-file lock.
    stream.seek(0)
    # msvcrt exists only on Windows; mypy checks against this machine's stubs.
    windows = cast(Any, msvcrt)
    try:
        windows.locking(stream.fileno(), windows.LK_NBLCK, 1)
    except OSError as error:
        if error.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
            return False
        raise
    return True


def acquire_exclusive(stream: BinaryIO, *, blocking: bool = True) -> bool:
    """Acquire an exclusive lock; return False only for nonblocking contention.

    Windows locks byte zero, including for an empty file. Blocking acquisition retries
    the nonblocking CRT call, avoiding its fixed ten-attempt blocking limit.
    Permission/open errors and unexpected IO errors propagate to the owner.
    """
    if os.name == 'nt':
        while not _try_windows_lock(stream):
            if not blocking:
                return False
            time.sleep(RETRY_SECONDS)
        return True
    import fcntl

    try:
        fcntl.flock(stream, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
    except BlockingIOError:
        return False
    return True


def release_exclusive(stream: BinaryIO) -> None:
    """Release a held lock without closing the caller's stream."""
    if os.name == 'nt':
        import msvcrt

        stream.seek(0)
        # msvcrt exists only on Windows; mypy checks against this machine's stubs.
        windows = cast(Any, msvcrt)
        windows.locking(stream.fileno(), windows.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(stream, fcntl.LOCK_UN)
