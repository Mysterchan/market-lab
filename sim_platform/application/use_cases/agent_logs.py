"""One setup read and one end-of-run archive; no tool-call database operations."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Optional

from sim_platform.domain.json_values import copy_json
from sim_platform.domain.models import ContractError, Run

if TYPE_CHECKING:  # Annotation only.
    from sim_platform.domain.agents import PreparedAgentExperiment
    from .runs import RunService

# The unit of work's persistence Repositories; use cases may not import persistence.
Repositories = Any
# A parsed Agent log: 'header', 'records' and 'end' (persistence AgentLogArchive).
AgentLogArchive = Dict[str, Any]


class AgentLogService:
    """Agent activity log binding and archiving inside the caller's unit of work."""

    def __init__(self, repositories: Repositories, runs: RunService) -> None:
        self.repo = repositories
        self.runs = runs

    @staticmethod
    def _validate_archive_identity(header: Dict[str, Any], profile_id: str, run_id: str) -> None:
        if header['run_id'] != run_id:
            raise ContractError('agent log belongs to another run or profile')
        if header['profile_id'] != profile_id:
            raise ContractError('agent log belongs to another run or profile')

    @staticmethod
    def _validate_experiment_binding(
        header: Dict[str, Any], experiment: PreparedAgentExperiment, run: Run
    ) -> None:
        if run.account_id != experiment.account_id:
            raise ContractError('agent log binding differs from its experiment')
        if header['source_ref'] != experiment.config['source_manifest_ref']:
            raise ContractError('agent log binding differs from its experiment')

    @staticmethod
    def _validate_run_end(run: Run, recorded_status: str) -> None:
        if run.status == 'running':
            raise ContractError('archive only after the run ends with the recorded status')
        if run.status != recorded_status:
            raise ContractError('archive only after the run ends with the recorded status')

    def agent_log_header(self, profile_id: str, experiment_id: str, run_id: str) -> Dict[str, Any]:
        """Bind the prepared experiment to an owned run; do not read source files."""
        experiment = self.repo.agent_experiments.owned(experiment_id, profile_id)
        run = self.runs.owned_run(profile_id, run_id)
        if run.account_id != experiment.account_id:
            raise ContractError('agent log run uses another account')
        if run.status != 'running':
            raise ContractError('agent log requires a running run')
        return dict(
            profile_id=profile_id,
            experiment_id=experiment_id,
            run_id=run_id,
            source_ref=experiment.config['source_manifest_ref'],
        )

    def archive_agent_log(
        self, profile_id: str, run_id: str, archive: AgentLogArchive
    ) -> Dict[str, Any]:
        """Save an already format-validated archive after checking its DB bindings.

        Platform reads and validates JSON before entering this UOW. Here the
        repositories check ownership; the helpers check identity, experiment
        bindings and terminal state. No file IO or format validation is repeated.
        """
        header = archive['header']
        self._validate_archive_identity(header, profile_id, run_id)

        experiment = self.repo.agent_experiments.owned(header['experiment_id'], profile_id)
        run = self.runs.owned_run(profile_id, run_id)
        self._validate_experiment_binding(header, experiment, run)
        self._validate_run_end(run, archive['end']['status'])

        self.repo.runs.save_agent_operations(run_id, archive)
        return dict(run_id=run_id, record_count=len(archive['records']))

    def agent_log_archive(self, profile_id: str, run_id: str) -> Optional[Any]:
        """Read recorded operations without changing the existing decision API."""
        self.runs.owned_run(profile_id, run_id)
        archive = self.repo.runs.archive(run_id)
        if archive is None:
            return None
        return copy_json(archive.get('agent_operations'))
