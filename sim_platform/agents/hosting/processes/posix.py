"""Own a private POSIX session/process group without touching the App's group."""

from __future__ import annotations

import os
import signal
from typing import TYPE_CHECKING, Optional, cast

if TYPE_CHECKING:  # Annotation only.
    from multiprocessing.process import BaseProcess


class PosixProcessFamily:
    """The Agent's own POSIX process group, so all its descendants can be signalled.

    The host creates one per Agent process; the child joins the group itself with
    setsid() before confirm(). close() has nothing to release.
    """

    name = 'posix_process_group'

    def __init__(self) -> None:
        self._pid: Optional[int] = None
        self._group_ready = False

    def attach(self, process: BaseProcess) -> None:
        self._pid = process.pid

    def confirm(self) -> None:
        """Child's ownership handshake confirms setsid completed before Agent setup."""
        self._group_ready = True

    def terminate(self, process: BaseProcess, *, force: bool = False) -> None:
        """Signal all ordinary descendants, even if their original parent exited."""
        if self._group_ready:
            try:
                # confirm() follows attach(), so the group's pid is known here.
                os.killpg(cast(int, self._pid), signal.SIGKILL if force else signal.SIGTERM)
            except ProcessLookupError:
                pass
        elif process.is_alive():
            # Initialization failed before the group handshake; no user code ran.
            process.kill() if force else process.terminate()

    def close(self) -> None:
        """No parent OS handles are retained for a POSIX process group."""
