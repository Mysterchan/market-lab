"""CPython Windows spawn with selected Python; inherited capabilities use native reducers.

Typing: the _winapi, msvcrt and popen_spawn_win32 names used here are declared in the
type stubs only for Windows, and the type check runs on macOS and Linux, so their lines
carry attr-defined ignores. They are not suspected bugs.
"""

from __future__ import annotations

import os
import msvcrt
import _winapi
import subprocess
from pathlib import Path
from multiprocessing import reduction, util
from multiprocessing.context import set_spawning_popen
from multiprocessing.popen_spawn_win32 import Popen, _close_handles  # type: ignore[attr-defined]
from typing import Any, Dict, Optional, Tuple

from .interpreter import preparation_data, command


def executable_environment(executable: str) -> Tuple[str, Optional[Dict[str, str]]]:
    """Bypass Windows venv redirectors so the owned PID is the actual Agent process."""
    configuration = Path(executable).parent.parent / 'pyvenv.cfg'
    if not configuration.is_file():
        return executable, None
    values = dict(
        line.split('=', 1) for line in configuration.read_text().splitlines() if '=' in line
    )
    home = next((value.strip() for key, value in values.items() if key.strip() == 'home'), None)
    base = Path(home) / 'python.exe' if home else None
    if base is None or not base.is_file():
        raise OSError('selected environment has no usable base Python executable')
    environment = dict(os.environ, __PYVENV_LAUNCHER__=executable)
    return str(base), environment


class InterpreterPopen(Popen):
    """CPython's Windows spawn Popen, launching the process's chosen interpreter instead."""

    def __init__(self, process: Any) -> None:
        executable, environment = executable_environment(process.executable)
        read_handle, write_handle = _winapi.CreatePipe(None, 0)  # type: ignore[attr-defined]
        descriptor = msvcrt.open_osfhandle(write_handle, 0)  # type: ignore[attr-defined]
        arguments = command(process.executable, parent_pid=os.getpid(), pipe_handle=read_handle)
        with open(descriptor, 'wb', closefd=True) as stream:
            try:
                handle, thread, pid, _ = _winapi.CreateProcess(  # type: ignore[attr-defined]
                    executable,
                    subprocess.list2cmdline(arguments),
                    None,
                    None,
                    False,
                    0,
                    environment,
                    process.project,
                    None,
                )
                _winapi.CloseHandle(thread)  # type: ignore[attr-defined]
            except BaseException:
                _winapi.CloseHandle(read_handle)  # type: ignore[attr-defined]
                raise
            self.pid, self.returncode = pid, None
            self._handle, self.sentinel = handle, int(handle)
            self.finalizer = util.Finalize(self, _close_handles, (self.sentinel, int(read_handle)))
            set_spawning_popen(self)
            try:
                reduction.dump(preparation_data(process), stream)
                reduction.dump(process, stream)
            finally:
                set_spawning_popen(None)
