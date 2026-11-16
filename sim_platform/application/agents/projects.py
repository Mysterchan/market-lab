"""Connect static project inspection, private files and the existing Agent revisions."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple, Union, cast

from sim_platform.agents.projects.inspection import inspect_project
from sim_platform.domain.models import ContractError
from sim_platform.domain.json_values import copy_json
from sim_platform.application.identifiers import identifier
from sim_platform.resources import cleanup_after_error

if TYPE_CHECKING:  # Annotation only.
    from os import PathLike

    from sim_platform.application.api_types import AgentRevisionDetails
    from sim_platform.application.operations import DatabaseOperations
    from sim_platform.persistence.agents.projects import ProjectFiles

# Tools an imported Agent may use when the caller does not list them.
DEFAULT_REQUIRES = ['market.quote', 'control.finish', 'model.request']
# Default prompt and schedule for an imported strategy.
DEFAULT_INITIAL_PROMPT = 'Evaluate trading opportunities; no trade is required.'
DEFAULT_INTERVAL_SECONDS = '30'


class AgentProjects:
    """Imports Agent source projects as private snapshots plus Agent revisions.

    Platform creates one with its database operations and project files; it holds
    no other state.
    """

    def __init__(self, operations: DatabaseOperations, files: ProjectFiles) -> None:
        self.operations, self.files = operations, files

    def check(self, source: Union[str, PathLike[str]]) -> Dict[str, Any]:
        """Inspect a project without saving it; includes the excluded file list."""
        files, excluded = self.files.capture(source)
        return dict(inspect_project(files), excluded=excluded)

    def import_project(
        self,
        profile: str,
        source: Union[str, PathLike[str]],
        *,
        name: Optional[str] = None,
        agent_id: Optional[str] = None,
        expected_revision: Optional[int] = None,
        integration: str = 'python_strategy',
        initial_prompt: str = DEFAULT_INITIAL_PROMPT,
        parameters: Optional[Dict[str, Any]] = None,
        requires: Optional[List[str]] = None,
        model_profile_ref: str = 'user-model',
        schedule: Optional[Dict[str, Any]] = None,
    ) -> AgentRevisionDetails:
        """Publish files first, then append one Agent revision in a short existing SQL UOW."""
        if integration not in ('python_strategy', 'python_carrier'):
            raise ContractError('imported source requires python_strategy or python_carrier')
        if expected_revision is not None:
            if agent_id is None:
                raise ContractError('an update needs agent_id and expected_revision')
            self.operations.agent(profile, agent_id, expected_revision)
        captured, excluded = self.files.capture(source)
        report = dict(inspect_project(captured), excluded=excluded)
        snapshot = self.files.save(captured, dict(report, profile_id=profile))
        try:
            definition = dict(
                agent_id=identifier() if agent_id is None else agent_id,
                revision=str(1 if expected_revision is None else expected_revision + 1),
                name=Path(source).stem if name is None else name,
                integration=integration,
                carrier='custom' if integration == 'python_carrier' else 'platform',
                initial_prompt=initial_prompt,
                parameters={} if parameters is None else parameters,
                requires=(list(DEFAULT_REQUIRES) if requires is None else requires),
                model_profile_ref=model_profile_ref,
                package=dict(project_ref=snapshot['project_ref']),
            )
            if integration == 'python_strategy':
                definition['schedule'] = (
                    schedule
                    if schedule is not None
                    else dict(
                        mode='interval',
                        interval_seconds=DEFAULT_INTERVAL_SECONDS,
                        busy_event_policy='preserve_interval',
                        events=[],
                    )
                )
            elif schedule is not None:
                raise ContractError('resident carriers do not accept a platform schedule')
            if expected_revision is None:
                return self.operations.create_agent(profile, definition)
            # An update was checked above to name its agent_id.
            return self.operations.update_agent(
                profile, cast(str, agent_id), expected_revision, definition
            )
        except Exception as exc:
            cleanup_after_error(exc, lambda: self.files.discard(snapshot['project_ref']))
            raise

    def project(
        self, profile: str, agent_id: str, revision: Optional[int] = None
    ) -> Tuple[Dict[str, str], Dict[str, Any]]:
        """Resolve ownership through SQL before accessing a private project snapshot."""
        saved = self.operations.agent(profile, agent_id, revision)
        package = cast(Optional[Dict[str, Any]], saved['definition'].get('package'))
        if not package:
            raise ContractError('this Agent uses the built-in template and has no source project')
        files, report = self.files.read(package['project_ref'], profile)
        inspected = inspect_project(files, default_python=report['python'])
        if any(report.get(key) != value for key, value in inspected.items()):
            raise ContractError('saved Agent project inspection differs from its metadata')
        return files, dict(copy_json(report), agent_id=agent_id, revision=saved['revision'])
