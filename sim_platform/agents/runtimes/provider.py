"""Use a trusted uv executable to download native standalone CPython builds."""

from __future__ import annotations

from pathlib import Path
import shutil
import sys
from typing import TYPE_CHECKING, Any, Callable, Optional, Union, cast
from sim_platform.agents.preparation.process import run_command
from sim_platform.agents.preparation.errors import PreparationError

if TYPE_CHECKING:  # Annotation only.
    from os import PathLike


# Seconds allowed for a Python download.
DOWNLOAD_TIMEOUT_SECONDS = 300


class UvPythonProvider:
    """Downloads standalone CPython builds with uv (bundled in a frozen app, else on PATH)."""

    def __init__(self, executable: Optional[Union[str, PathLike[str]]] = None) -> None:
        if executable is None and getattr(sys, 'frozen', False):
            # _MEIPASS is set by the bundler in a frozen app; the stubs do not declare it.
            executable = (
                Path(cast(Any, sys)._MEIPASS)
                / 'helpers'
                / ('uv.exe' if sys.platform == 'win32' else 'uv')
            )
        self.executable = str(executable) if executable is not None else None

    def download(self, target: Path, request: str, cancelled: Callable[[], bool]) -> None:
        executable = self.executable or shutil.which('uv')
        if executable is None or not Path(executable).is_file():
            raise PreparationError(
                'PYTHON_MANAGER_UNAVAILABLE',
                'configure the bundled uv executable or install uv for repository use',
            )
        run_command(
            [
                executable,
                '--no-config',
                '--no-progress',
                '--cache-dir',
                str(target / 'cache'),
                'python',
                'install',
                '--install-dir',
                str(target / 'installation'),
                '--no-bin',
                '--no-registry',
                'cpython@' + request,
            ],
            cancelled,
            timeout=DOWNLOAD_TIMEOUT_SECONDS,
            error_code='PYTHON_DOWNLOAD_FAILED',
            environment={
                'UV_PYTHON_INSTALL_DIR': str(target / 'installation'),
                'UV_PYTHON_INSTALL_BIN': '0',
            },
        )
