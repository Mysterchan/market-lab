"""CPython spawn adapter with an explicit executable and isolated preparation data.

Uses the existing multiprocessing handle reducers and resource tracker. The launch
sequence follows CPython's popen_spawn_posix; keep compatibility covered by actual
cross-interpreter tests rather than changing the process-wide executable.
"""

from __future__ import annotations

import io
import os
from typing import Any, List, cast
from multiprocessing import reduction, resource_tracker, util
from multiprocessing.context import set_spawning_popen
from multiprocessing.popen_spawn_posix import Popen
from .interpreter import preparation_data, command


class InterpreterPopen(Popen):
    """CPython's POSIX spawn Popen, launching the process's chosen interpreter instead."""

    # Inherited from multiprocessing's Popen; the stubs do not declare it.
    _fds: List[int]

    def _launch(self, process: Any) -> None:
        tracker = resource_tracker.getfd()
        # The resource tracker is running in the parent, so its descriptor exists.
        self._fds.append(cast(int, tracker))
        data = io.BytesIO()
        set_spawning_popen(self)
        try:
            reduction.dump(preparation_data(process), data)
            reduction.dump(process, data)
        finally:
            set_spawning_popen(None)
        parent_read = parent_write = child_read = child_write = None
        try:
            parent_read, child_write = os.pipe()
            child_read, parent_write = os.pipe()
            self._fds.extend([child_read, child_write])
            arguments = command(process.executable, tracker_fd=tracker, pipe_handle=child_read)
            program = process.executable
            if getattr(process, 'sandbox', None):
                import shutil

                from .sandbox import wrap

                os.makedirs(os.path.join(process.project, '.tmp'), exist_ok=True)
                arguments = wrap(
                    arguments, process.sandbox, workspace=process.project, descriptors=self._fds
                )
                program = shutil.which(arguments[0]) or arguments[0]
            self.pid = util.spawnv_passfds(os.fsencode(program), arguments, self._fds)
            self.sentinel = parent_read
            with open(parent_write, 'wb', closefd=False) as stream:
                stream.write(data.getbuffer())
        finally:
            owned = [fd for fd in (parent_read, parent_write) if fd is not None]
            # util.close_fds exists at runtime but is not declared in the stubs.
            self.finalizer = util.Finalize(self, cast(Any, util).close_fds, owned)
            for fd in (child_read, child_write):
                if fd is not None:
                    os.close(fd)
