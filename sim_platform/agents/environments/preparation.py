"""Prepare dependencies using the Agent's resolved interpreter, independent of the App."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Optional, Union

from sim_platform.agents.preparation.job import PreparationJob
from sim_platform.agents.projects.syntax import validate_with_python
from .installer import install_environment
from sim_platform.sdk.distribution import identity
from sim_platform.resources import cleanup_after_error

if TYPE_CHECKING:  # Annotation only.
    from os import PathLike

    from sim_platform.agents.runtimes.manager import PythonRuntimes


def environment_specification(project: Dict[str, Any], runtime: Dict[str, Any]) -> Dict[str, Any]:
    """What identifies an environment: Python request, requirements, runtime and SDK."""
    return dict(
        python=project['python'],
        requirements=project['requirements'],
        runtime=runtime,
        sdk=identity(),
    )


class EnvironmentPreparation(PreparationJob):
    """Background job that prepares a project's Python environment; started on construction.

    ``files`` and ``sdk_files`` are persistence's EnvironmentFiles and SDKFiles, which
    agents may not import.
    """

    def __init__(
        self,
        files: Any,
        project: Dict[str, Any],
        runtimes: PythonRuntimes,
        source_files: Dict[str, str],
        sdk_files: Any,
        *,
        wheelhouse: Optional[Union[str, PathLike[str]]] = None,
        allow_download: bool = False,
        allow_python_download: bool = False,
        python_archive: Optional[Union[str, PathLike[str]]] = None,
    ) -> None:
        super().__init__('agent-environment-preparation')
        self.files, self.project = files, project
        self.runtimes, self.source_files = runtimes, source_files
        self.sdk_files = sdk_files
        self.wheelhouse, self.allow_download = wheelhouse, allow_download
        self.allow_python_download, self.python_archive = allow_python_download, python_archive
        self.update(environment=None, runtime=None)
        self.start()

    def perform(self) -> None:
        runtime, _ = self.runtimes.prepare(
            self.project['python'],
            self._cancel.is_set,
            self.phase,
            allow_download=self.allow_python_download,
            archive=self.python_archive,
        )
        self.update(runtime=runtime)
        self.phase('checking_project')
        validate_with_python(runtime['executable'], self.source_files, self._cancel.is_set)
        specification = environment_specification(self.project, runtime)
        self.phase('waiting_for_environment')
        with self.files.lease(self._cancel.is_set) as acquired:
            if not acquired:
                from sim_platform.agents.preparation.errors import PreparationCancelled

                raise PreparationCancelled()
            existing = self.files.find(specification)
            if existing is not None:
                self.update(state='ready', phase='ready', environment=existing, reused=True)
                return
            target = self.files.allocate()
            try:
                packages = install_environment(
                    target,
                    specification,
                    self._cancel.is_set,
                    self.phase,
                    sdk_wheel=self.sdk_files.wheel(),
                    wheelhouse=self.wheelhouse,
                    allow_download=self.allow_download,
                )
                if self._cancel.is_set():
                    from sim_platform.agents.preparation.errors import PreparationCancelled

                    raise PreparationCancelled()
                environment = self.files.publish(target, specification, packages)
                self.update(state='ready', phase='ready', environment=environment)
            except Exception as exc:
                cleanup_after_error(exc, lambda: self.files.discard(target))
                raise
