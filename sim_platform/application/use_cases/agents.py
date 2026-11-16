"""Agent registration and experiment preparation in one caller-owned UOW.

Read the public methods on AgentService first. Repositories handle persistence;
configuration validators handle input contracts; this service coordinates
ownership, revision conflicts, and snapshot capture. It never opens a session
or commits: DatabaseOperations owns the transaction, including rollback.

Prepared experiments are saved data, not running runs. Their referenced model,
source, support profile, and Python environment still need runtime binding.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from dataclasses import asdict

from sim_platform.agents.configuration import (
    AgentConfigurationError,
    validate_definition,
    validate_experiment_settings,
    validate_identifier,
    validate_revision,
    validate_run_configuration,
)
from sim_platform.domain.agents import AgentRevision, PreparedAgentExperiment
from sim_platform.domain.json_values import copy_json
from sim_platform.domain.models import ContractError
from sim_platform.execution.models import ExecutionConfig

from sim_platform.application.identifiers import identifier as new_identifier

if TYPE_CHECKING:  # Annotation only.
    from .accounts import AccountService

# The unit of work's persistence Repositories; use cases may not import persistence.
Repositories = Any


class AgentService:
    """Coordinate agent operations using repositories from the current UOW.

    accounts is the existing AccountService from that same UOW. Public methods
    return detached dictionaries; returned values cannot alter persisted data.
    """

    def __init__(self, repositories: Repositories, accounts: AccountService) -> None:
        self.repo = repositories
        self.accounts = accounts

    def create_agent(self, profile_id: str, definition: dict) -> dict:
        """Save revision 1 for an existing profile; reject an already used ID."""
        self.repo.profiles.get(profile_id)
        validated = validate_definition(definition)
        if validated['revision'] != '1':
            raise AgentConfigurationError('INVALID_ARGUMENT', 'a new agent starts at revision 1')
        if self.repo.agents.latest(validated['agent_id']) is not None:
            raise AgentConfigurationError('REVISION_CONFLICT', 'agent ID already exists')

        revision = AgentRevision(
            agent_id=validated['agent_id'],
            revision=1,
            profile_id=profile_id,
            definition=validated,
        )
        self.repo.agents.add(revision)
        return asdict(revision)

    def update_agent(
        self,
        profile_id: str,
        agent_id: str,
        expected_revision: int,
        definition: dict,
    ) -> dict:
        """Append a full next manifest, retaining every earlier revision.

        The existing write UOW serializes the comparison and insert. A stale
        expected_revision raises REVISION_CONFLICT rather than overwriting.
        """
        validate_identifier(agent_id)
        validate_revision(expected_revision, allow_successor=True)
        current = self.repo.agents.owned(agent_id, profile_id)
        if current.revision != expected_revision:
            raise AgentConfigurationError(
                'REVISION_CONFLICT',
                'agent has changed; read its current revision',
            )

        validated = validate_definition(definition)
        next_revision = expected_revision + 1
        if validated['agent_id'] != agent_id or validated['revision'] != str(next_revision):
            raise AgentConfigurationError(
                'INVALID_ARGUMENT',
                'update requires the same identity and next revision',
            )
        revision = AgentRevision(
            agent_id=agent_id,
            revision=next_revision,
            profile_id=profile_id,
            definition=validated,
        )
        self.repo.agents.add(revision)
        return asdict(revision)

    def agent(self, profile_id: str, agent_id: str, revision: Optional[int] = None) -> dict:
        """Read an owned revision, or the latest revision when omitted."""
        validate_identifier(agent_id)
        if revision is not None:
            validate_revision(revision)
        saved = self.repo.agents.owned(agent_id, profile_id, revision)
        validate_definition(saved.definition)
        return asdict(saved)

    def agents(self, profile_id: str, limit: int = 100, after_id: Optional[str] = None) -> list:
        """Read latest revisions ordered by agent ID; page size is 1 to 100."""
        self._validate_page(profile_id, limit, after_id)
        revisions = self.repo.agents.for_profile(profile_id, limit, after_id)
        for revision in revisions:
            validate_definition(revision.definition)
        return [asdict(revision) for revision in revisions]

    def prepare_agent_experiment(
        self,
        profile_id: str,
        account_id: str,
        agent_id: str,
        revision: int,
        settings: dict,
        execution_config: Optional[ExecutionConfig] = None,
    ) -> dict:
        """Freeze a selected revision, caller settings, and current account.

        settings excludes the four platform-owned run fields. The account must
        have no active run or accepted/partial orders. Save everything in the
        current UOW; do not reserve the account or create a running run.
        """
        validate_revision(revision)
        validate_identifier(agent_id)
        selected_agent = self.repo.agents.owned(agent_id, profile_id, revision)
        self.repo.accounts.owned(account_id, profile_id)
        caller_settings = validate_experiment_settings(settings)

        initial_snapshot = self._capture_initial_account(profile_id, account_id)
        execution_snapshot = self._execution_snapshot(execution_config)
        experiment_id = new_identifier()
        effective_config = self._build_run_configuration(
            experiment_id,
            selected_agent.definition,
            caller_settings,
        )
        experiment = PreparedAgentExperiment(
            id=experiment_id,
            profile_id=profile_id,
            account_id=account_id,
            agent_id=agent_id,
            revision=revision,
            config=effective_config,
            initial_snapshot=initial_snapshot,
            execution_config=execution_snapshot,
        )
        self.repo.agent_experiments.add(experiment)
        return self._prepared_details(experiment)

    def agent_experiment(self, profile_id: str, experiment_id: str) -> dict:
        """Read and validate a full owned snapshot without starting execution."""
        validate_identifier(experiment_id)
        experiment = self.repo.agent_experiments.owned(experiment_id, profile_id)
        validate_run_configuration(experiment.config)
        ExecutionConfig(**experiment.execution_config)
        return self._prepared_details(experiment)

    def agent_experiments(
        self,
        profile_id: str,
        limit: int = 100,
        after_id: Optional[str] = None,
    ) -> list:
        """Read ID-ordered summaries, excluding full configuration/account data."""
        self._validate_page(profile_id, limit, after_id)
        summaries = self.repo.agent_experiments.for_profile(profile_id, limit, after_id)
        return [
            dict(summary, state='prepared', readiness='requires_runtime_binding')
            for summary in summaries
        ]

    def _validate_page(self, profile_id: str, limit: int, after_id: Optional[str]) -> None:
        self.repo.profiles.get(profile_id)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise AgentConfigurationError(
                'INVALID_ARGUMENT',
                'integer is outside the supported range',
            )
        if after_id is not None:
            validate_identifier(after_id)

    def _capture_initial_account(self, profile_id: str, account_id: str) -> dict:
        """Check eligibility and capture canonical JSON in the same transaction."""
        if self.repo.runs.active_for_account(account_id):
            raise ContractError('account already has an active run')
        snapshot = self.accounts.snapshot(profile_id, account_id)
        initial_snapshot = copy_json(asdict(snapshot))
        if any(order['status'] in ('accepted', 'partial') for order in initial_snapshot['orders']):
            raise ContractError('resolve existing orders before preparing an agent experiment')
        return initial_snapshot

    @staticmethod
    def _execution_snapshot(execution_config: Optional[ExecutionConfig]) -> dict:
        """Reconstruct to validate even a frozen instance modified by caller code."""
        config = ExecutionConfig() if execution_config is None else execution_config
        if not isinstance(config, ExecutionConfig):
            raise ContractError('expected ExecutionConfig')
        try:
            validated = ExecutionConfig(**asdict(config))
        except TypeError as exc:
            raise ContractError('invalid execution configuration') from exc
        return asdict(validated)

    @staticmethod
    def _build_run_configuration(experiment_id: str, definition: dict, settings: dict) -> dict:
        """Add platform identities and validate cross-section run constraints."""
        config = {
            **settings,
            'run_id': experiment_id,
            'definition': definition,
            'initial_account_ref': experiment_id + ':initial-account',
            'execution_config_ref': experiment_id + ':execution',
        }
        return validate_run_configuration(config)

    @staticmethod
    def _prepared_details(experiment: PreparedAgentExperiment) -> dict:
        """Describe saved data and unresolved bindings; perform no reference IO."""
        config = experiment.config
        definition = config['definition']
        bindings = {
            'model_profile_ref': definition['model_profile_ref'],
            'model_profile_revision': config['model_profile_revision'],
            'source_manifest_ref': config['source_manifest_ref'],
            'support_profile_ref': config['support_profile_ref'],
            'lag_profile_ref': config['lag']['profile_ref'],
        }
        package = definition.get('package')
        if package:
            bindings['project_ref'] = package['project_ref']
        return {
            **asdict(experiment),
            'state': 'prepared',
            'readiness': 'requires_runtime_binding',
            'bindings_required': bindings,
        }
