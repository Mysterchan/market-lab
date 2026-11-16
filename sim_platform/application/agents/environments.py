"""Resolve saved Agent environments and child launch inputs from injected capabilities.

This service checks projects and assembles preparation jobs; Platform registers
jobs as roots. A successful launch transfers its temporary source workspace to
the process host. No SQL transaction, process host or Platform is created here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Optional, Tuple, Union

from sim_platform.agents.environments.preparation import (
    EnvironmentPreparation,
    environment_specification,
)
from sim_platform.domain.models import ContractError
from sim_platform.application.experiments.contracts import AgentLaunch

if TYPE_CHECKING:
    from os import PathLike

    from sim_platform.application.agents.projects import AgentProjects
    from sim_platform.agents.runtimes.manager import PythonRuntimes
    from sim_platform.persistence.agents.environments import EnvironmentFiles
    from sim_platform.persistence.agents.sdk import SDKFiles
    from sim_platform.persistence.workspace.temporary import AppWorkspace


class AgentEnvironments:
    """Finds and prepares Python environments for saved Agent projects, and launch inputs.

    Platform creates one and keeps it for its lifetime; it holds only the injected
    project, environment, runtime and SDK capabilities.
    """

    def __init__(
        self,
        projects: AgentProjects,
        files: EnvironmentFiles,
        runtimes: PythonRuntimes,
        sdk: SDKFiles,
    ) -> None:
        self.projects, self.files, self.runtimes, self.sdk = projects, files, runtimes, sdk

    def _ready(self, project: dict) -> Optional[dict]:
        runtime = self.runtimes.ready(project['python'])
        return (
            self.files.find(environment_specification(project, runtime))
            if runtime is not None
            else None
        )

    def ready(self, profile: str, agent_id: str, revision: Optional[int] = None) -> Optional[dict]:
        """Read the owned project and find its compatible published environment."""
        _, project = self.projects.project(profile, agent_id, revision)
        return self._ready(project)

    def prepare(
        self,
        profile: str,
        agent_id: str,
        revision: Optional[int] = None,
        *,
        wheelhouse: Optional[Union[str, PathLike[str]]] = None,
        allow_download: bool = False,
        allow_python_download: bool = False,
        python_archive: Optional[Union[str, PathLike[str]]] = None,
    ) -> EnvironmentPreparation:
        """Resolve one revision before starting a job; caller owns registration/close."""
        if type(allow_download) is not bool:
            raise ContractError('allow_download must be boolean')
        if type(allow_python_download) is not bool:
            raise ContractError('allow_python_download must be boolean')
        source, project = self.projects.project(profile, agent_id, revision)
        return EnvironmentPreparation(
            self.files,
            project,
            self.runtimes,
            source,
            self.sdk,
            wheelhouse=wheelhouse,
            allow_download=allow_download,
            allow_python_download=allow_python_download,
            python_archive=python_archive,
        )

    def launch(self, profile: str, definition: dict, workspace: AppWorkspace) -> AgentLaunch:
        """Validate the saved reference and environment before allocating source files."""
        files, project = self.projects.project(
            profile, definition['agent_id'], int(definition['revision'])
        )
        if project['project_ref'] != definition['package']['project_ref']:
            raise ContractError('selected Agent source differs from the saved run definition')
        environment = self._ready(project)
        if environment is None:
            raise ContractError(
                'Agent environment is not ready; call prepare_agent_environment first'
            )
        executable = str(self.files.executable(environment))
        source = workspace.agent_source(files)
        return dict(executable=executable, project=str(source.directory), workspace=source)

    def _model_project(
        self, source: Union[str, PathLike[str]]
    ) -> Tuple[Dict[str, str], Dict[str, Any]]:
        """Read a model folder's requirements.txt and model.json, as for an Agent project.

        A model project is a single .py file or a folder; only ``requirements.txt`` and an
        optional ``model.json`` containing ``{"python": "3.12"}`` select the environment.
        """
        import json

        from sim_platform.agents.projects.inspection import requirements
        from sim_platform.agents.runtimes.versions import DEFAULT_PYTHON, PythonRequest

        files, _ = self.projects.files.capture(source)
        settings: Any = json.loads(files['model.json']) if 'model.json' in files else {}
        if type(settings) is not dict or set(settings) - {'python'}:
            raise ContractError('model.json only accepts the python setting')
        python = settings.get('python', DEFAULT_PYTHON)
        PythonRequest(python)
        project = dict(python=python, requirements=requirements(files.get('requirements.txt', '')))
        return files, project

    def prepare_model(
        self,
        source: Union[str, PathLike[str]],
        *,
        wheelhouse: Optional[Union[str, PathLike[str]]] = None,
        allow_download: bool = False,
        allow_python_download: bool = False,
        python_archive: Optional[Union[str, PathLike[str]]] = None,
    ) -> EnvironmentPreparation:
        """Start preparing a model project's environment; caller owns registration/close."""
        if type(allow_download) is not bool or type(allow_python_download) is not bool:
            raise ContractError('download choices must be boolean')
        files, project = self._model_project(source)
        return EnvironmentPreparation(
            self.files,
            project,
            self.runtimes,
            files,
            self.sdk,
            wheelhouse=wheelhouse,
            allow_download=allow_download,
            allow_python_download=allow_python_download,
            python_archive=python_archive,
        )

    def model_python(self, source: Union[str, PathLike[str]]) -> Optional[str]:
        """The interpreter of a model project's ready environment, or None if not prepared."""
        _, project = self._model_project(source)
        environment = self._ready(project)
        return str(self.files.executable(environment)) if environment is not None else None
