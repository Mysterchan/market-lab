"""Owned temporary preparation process; progress never blocks the native builder."""

from __future__ import annotations
import logging

import multiprocessing
from pathlib import Path
import shutil
import tempfile
import time
from typing import Any, Dict, Optional

from sim_platform.domain.models import ContractError

from .builder import build

_log = logging.getLogger(__name__)

# Error text sent back from the child process is cut to this length.
_ERROR_LIMIT = 1024


def run_preparation(
    outgoing: Any, cancel: Any, frames: Any, source: str, destination: str, options: Dict[str, Any]
) -> None:
    """Child-process entry point: build the artifact and send back its path or an error.

    ``cancel`` and ``frames`` are shared values, so the parent can request cancellation
    and read progress without exchanging messages with the builder.
    """
    try:
        path = build(
            source,
            destination,
            cancelled=lambda: bool(cancel.value),
            progress=lambda value: setattr(frames, 'value', value),
            **options,
        )
        outgoing.send(dict(path=str(path), error=None))
    except Exception as exc:
        _log.warning('Handled failure in run_preparation', exc_info=True)
        outgoing.send(dict(path=None, error=(type(exc).__name__ + ': ' + str(exc))[:_ERROR_LIMIT]))
    finally:
        outgoing.close()


class PreparationJob:
    """One preparation running in its own process, which this object owns.

    The job starts immediately. ``snapshot()`` reports progress, ``cancel()`` asks the
    builder to stop, ``result()`` waits for the finished artifact and ``close()``
    always ends the process and removes its staging directory.
    """

    def __init__(self, source: Any, destination: Any, **options: Any) -> None:
        context = multiprocessing.get_context('spawn')
        self._cancel = context.RawValue('b', 0)
        self._frames = context.RawValue('Q', 0)
        self._incoming, outgoing = context.Pipe(duplex=False)
        destination = Path(destination).resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        self._stage = Path(tempfile.mkdtemp(prefix='.preparation-job-', dir=destination.parent))
        options = dict(options, _staging_parent=str(self._stage))
        self._process = context.Process(
            target=run_preparation,
            name='market-preparation',
            args=(outgoing, self._cancel, self._frames, str(source), str(destination), options),
        )
        self._closed = False
        self._result: Optional[Dict[str, Any]] = None
        try:
            self._process.start()
        except Exception:
            self._incoming.close()
            shutil.rmtree(self._stage)
            raise
        finally:
            outgoing.close()

    def snapshot(self) -> Dict[str, Any]:
        """Return source frames processed so far and whether the process is still running."""
        return dict(frames=self._frames.value, alive=not self._closed and self._process.is_alive())

    def cancel(self) -> None:
        self._cancel.value = 1

    def result(self, timeout: float = 60) -> Path:
        """Wait for the artifact directory.

        Raises:
            ContractError: If preparation failed or the process ended without a result.
            TimeoutError: If it is still running after ``timeout`` seconds.
        """
        deadline = time.monotonic() + timeout
        while self._result is None:
            if self._incoming.poll(0.02):
                try:
                    self._result = self._incoming.recv()
                except EOFError:
                    raise ContractError('preparation process exited without a result') from None
            elif not self._process.is_alive():
                raise ContractError('preparation process exited without a result')
            elif time.monotonic() >= deadline:
                raise TimeoutError('preparation is still running')
        if self._result['error']:
            raise ContractError(self._result['error'])
        return Path(self._result['path'])

    def close(self) -> None:
        """Stop the process, escalating from cancel to terminate to kill."""
        if self._closed:
            return
        self.cancel()
        self._process.join(2)
        if self._process.is_alive():
            self._process.terminate()
            self._process.join(1)
        if self._process.is_alive():
            self._process.kill()
            self._process.join(1)
        if self._process.is_alive():
            raise ContractError('preparation child did not exit; ownership retained')
        self._incoming.close()
        shutil.rmtree(self._stage)
        self._closed = True
