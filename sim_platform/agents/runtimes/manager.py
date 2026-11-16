"""Resolve compatible runtimes and publish only successfully probed installations."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, Optional, Tuple, Union

from sim_platform.agents.preparation.errors import PreparationError, PreparationCancelled
from sim_platform.agents.preparation.job import PreparationJob
from sim_platform.resources import cleanup_after_error
from .probe import inspect_python, app_python
from .versions import PythonRequest, version_tuple

if TYPE_CHECKING:  # Annotation only.
    from os import PathLike

    from .provider import UvPythonProvider

# A runtime record: 'version', 'executable', 'base_prefix', 'runtime_ref', 'origin', ...
RuntimeRecord = Dict[str, Any]
# Version range used for downloads when the request names no minor.
DOWNLOAD_RANGE = ',>=3.9,<3.15'


class PythonRuntimes:
    """Finds, downloads or imports a CPython for Agent projects.

    Platform creates one with the runtime files (persistence RuntimeFiles, which
    agents may not import) and the download provider; it holds no other state.
    """

    def __init__(self, files: Any, provider: UvPythonProvider) -> None:
        self.files, self.provider = files, provider

    def available(self) -> Any:
        return self.files.records()

    def find(self, request: str) -> Optional[RuntimeRecord]:
        """The newest managed runtime matching ``request``."""
        requirement = PythonRequest(request)
        candidates = [
            value for value in self.files.records() if requirement.matches(value['version'])
        ]
        return (
            max(candidates, key=lambda value: version_tuple(value['version']))
            if candidates
            else None
        )

    def ready(self, request: str) -> Optional[RuntimeRecord]:
        """Read managed metadata, or describe a compatible non-frozen App interpreter."""
        existing = self.find(request)
        if existing is not None:
            return existing
        borrowed = app_python()
        return (
            borrowed
            if borrowed is not None and PythonRequest(request).matches(borrowed['version'])
            else None
        )

    def prepare(
        self,
        request: str,
        cancelled: Callable[[], bool],
        phase: Callable[[str], None],
        *,
        allow_download: bool = False,
        archive: Optional[Union[str, PathLike[str]]] = None,
    ) -> Tuple[RuntimeRecord, bool]:
        """Return a ready runtime and whether it was reused, installing one if needed."""
        requirement = PythonRequest(request)
        phase('waiting_for_python')
        with self.files.lease(cancelled) as acquired:
            if not acquired:
                raise PreparationCancelled()
            existing = self.ready(request)
            if existing is not None and archive is None:
                return existing, True
            if archive is None and not allow_download:
                raise PreparationError(
                    'PYTHON_UNAVAILABLE',
                    'no compatible runtime is ready; enable Python downloads or import an offline archive',
                )
            target = self.files.allocate()
            try:
                if archive is not None:
                    phase('importing_python')

                    def check_cancelled() -> None:
                        if cancelled():
                            raise PreparationCancelled()

                    self.files.unpack(archive, target / 'installation', check_cancelled)
                    origin = 'offline'
                else:
                    phase('downloading_python')
                    selection = (
                        request if requirement.minor is not None else request + DOWNLOAD_RANGE
                    )
                    self.provider.download(target, selection, cancelled)
                    origin = 'download'
                phase('checking_python')
                binaries = sorted((target / 'installation').rglob('bin/python3'))
                if not binaries:
                    raise PreparationError(
                        'INVALID_PYTHON_RUNTIME',
                        'archive needs a standalone installation containing bin/python3',
                    )
                details = inspect_python(binaries[0], cancelled)
                if not requirement.matches(details['version']):
                    raise PreparationError(
                        'PYTHON_VERSION_MISMATCH',
                        'installed Python does not satisfy the project request',
                    )
                try:
                    Path(details['base_prefix']).resolve().relative_to(target.resolve())
                    Path(details['executable']).resolve().relative_to(target.resolve())
                except ValueError as exc:
                    raise PreparationError(
                        'INVALID_PYTHON_RUNTIME',
                        'runtime must be self-contained inside its installation',
                    ) from exc
                if cancelled():
                    raise PreparationCancelled()
                return self.files.publish(target, details, origin), False
            except Exception as exc:
                cleanup_after_error(exc, lambda: self.files.discard(target))
                raise


class RuntimePreparation(PreparationJob):
    """Background job that prepares a Python runtime; started on construction."""

    def __init__(
        self,
        manager: PythonRuntimes,
        request: str,
        *,
        allow_download: bool = False,
        archive: Optional[Union[str, PathLike[str]]] = None,
    ) -> None:
        super().__init__('agent-python-preparation')
        self.manager, self.request = manager, request
        self.allow_download, self.archive = allow_download, archive
        self.update(runtime=None)
        self.start()

    def perform(self) -> None:
        runtime, reused = self.manager.prepare(
            self.request,
            self._cancel.is_set,
            self.phase,
            allow_download=self.allow_download,
            archive=self.archive,
        )
        self.update(state='ready', phase='ready', runtime=runtime, reused=reused)
