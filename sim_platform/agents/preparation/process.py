"""Run trusted preparation tools with bounded output, time and cancellation."""

from __future__ import annotations

import os
import signal
import subprocess
import tempfile
import time
from typing import IO, TYPE_CHECKING, Callable, Dict, Optional, Sequence, Tuple, Union

from .errors import PreparationError, PreparationCancelled

if TYPE_CHECKING:  # Annotation only.
    from sim_platform.agents.hosting.processes.windows import WindowsProcessFamily

# Environment variables a preparation command inherits (compared in upper case).
INHERITED_ENVIRONMENT = (
    'PATH',
    'HOME',
    'TMPDIR',
    'TEMP',
    'TMP',
    'SYSTEMROOT',
    'COMSPEC',
    'USERPROFILE',
    'LOCALAPPDATA',
)
# Most command output kept (1 MiB); default time limit; poll and reap waits.
MAX_OUTPUT_BYTES = 1024 * 1024
DEFAULT_TIMEOUT_SECONDS = 300
POLL_SECONDS = 0.05
REAP_WAIT_SECONDS = 1


def _stop_command(process: subprocess.Popen[bytes], family: Optional[WindowsProcessFamily]) -> None:
    """Reap the owned command and descendants before its temporary files close."""
    if family is not None:
        family.close()  # Windows Job close terminates remaining members.
    elif process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=REAP_WAIT_SECONDS)
    except subprocess.TimeoutExpired:
        if family is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            process.kill()
        process.wait(timeout=REAP_WAIT_SECONDS)


def _command_environment(extra: Optional[Dict[str, str]]) -> Dict[str, str]:
    """A minimal environment: inherited basics, no user site or pip config, plus ``extra``."""
    environment = {
        key: value for key, value in os.environ.items() if key.upper() in INHERITED_ENVIRONMENT
    }
    environment.update(PYTHONNOUSERSITE='1', PIP_CONFIG_FILE=os.devnull)
    environment.update(extra or {})
    return environment


def _launch_command(
    arguments: Sequence[str],
    source: Union[IO[bytes], int],
    output: IO[bytes],
    environment: Dict[str, str],
) -> Tuple[subprocess.Popen[bytes], Optional[WindowsProcessFamily]]:
    """Start the command in its own session (POSIX) or job object (Windows)."""
    family = None
    if os.name == 'nt':
        from sim_platform.agents.hosting.processes.windows import WindowsProcessFamily

        family = WindowsProcessFamily()
    process: Optional[subprocess.Popen[bytes]] = None
    try:
        process = subprocess.Popen(
            arguments,
            stdin=source,
            stdout=output,
            stderr=subprocess.STDOUT,
            env=environment,
            start_new_session=os.name == 'posix',
        )
        if family is not None:
            family.attach(process)
    except Exception:
        if family is not None:
            family.close()
        if process is not None:
            if process.poll() is None:
                process.kill()
            process.wait()
        raise
    return process, family


def run_command(
    arguments: Sequence[str],
    cancelled: Callable[[], bool],
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    input_data: Optional[str] = None,
    environment: Optional[Dict[str, str]] = None,
    error_code: str = 'DEPENDENCY_UNAVAILABLE',
) -> str:
    """Bound transient subprocess lifetime and output; terminate its process group on cancellation."""
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as source:
        if input_data is not None:
            source.write(input_data.encode())
            source.seek(0)
        process, family = _launch_command(
            arguments,
            source if input_data is not None else subprocess.DEVNULL,
            output,
            _command_environment(environment),
        )
        deadline = time.monotonic() + timeout
        try:
            while process.poll() is None:
                if cancelled():
                    raise PreparationCancelled()
                if (
                    time.monotonic() > deadline
                    or os.fstat(output.fileno()).st_size > MAX_OUTPUT_BYTES
                ):
                    raise PreparationError(
                        'PREPARATION_TIMEOUT',
                        'dependency preparation exceeded its time or output bound',
                    )
                time.sleep(POLL_SECONDS)
            if cancelled():
                raise PreparationCancelled()
            if process.returncode:
                raise PreparationError(
                    error_code,
                    'preparation command failed; check runtime availability and compatible wheels',
                )
            output.seek(0)
            return output.read(MAX_OUTPUT_BYTES).decode('utf-8', errors='replace')
        finally:
            _stop_command(process, family)
