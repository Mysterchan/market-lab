"""Atomic prepared-account check and execution run creation, within caller UOW."""

from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING, Any, Dict, Optional

from sim_platform.domain.json_values import copy_json
from sim_platform.domain.models import ContractError

if TYPE_CHECKING:  # Annotation only.
    from sim_platform.execution import ExecutionConfig
    from .accounts import AccountService
    from .agents import AgentService
    from .runs import RunService

# The unit of work's persistence Repositories; use cases may not import persistence.
Repositories = Any

# A created Agent run: 'maker_id', 'run_id', 'experiment_id' and 'config' (Agent config).
AgentRunBinding = Dict[str, Any]


class AgentRunService:
    """Agent run creation and setup-failure cleanup inside the caller's unit of work."""

    def __init__(
        self,
        repositories: Repositories,
        accounts: AccountService,
        runs: RunService,
        agents: AgentService,
    ) -> None:
        self.repo, self.accounts, self.runs = repositories, accounts, runs
        self.agents = agents

    def cancel_unbound_agent_run(self, profile_id: str, run_id: str) -> bool:
        """End failed setup with no execution history; never bypass a financial owner."""
        run = self.runs.owned_run(profile_id, run_id)
        if not run.config.get('agent_experiment_id'):
            raise ContractError('not an agent run')
        if self.repo.events.has_run(run_id):
            return False
        run.require_active()
        self.runs.record_run_event(
            run,
            run_id + ':end',
            0,
            'execution_end',
            dict(reason='agent setup failed before execution binding', status='stopped'),
        )
        self.runs.finish_run(profile_id, run_id, 'stopped')
        return True

    def create_agent_run(
        self, profile_id: str, experiment_id: str, source: Dict[str, Any]
    ) -> AgentRunBinding:
        """Create the run for a prepared experiment once its source and account still match."""
        from sim_platform.domain.experiment import validate_execution_mode

        validate_execution_mode(source.get("execution_mode"))
        experiment = self.repo.agent_experiments.owned(experiment_id, profile_id)
        config = copy_json(experiment.config)
        if (
            source['symbol'] != config['symbol']
            or source['source_manifest_ref'] != config['source_manifest_ref']
            or source.get('experiment_start_ns', source['start_origin']['time_ns'])
            != int(config['start_ns'])
        ):
            raise ContractError('prepared source binding differs from the experiment')
        current = copy_json(asdict(self.accounts.snapshot(profile_id, experiment.account_id)))
        if current != experiment.initial_snapshot:
            raise ContractError(
                'account changed since experiment preparation; prepare a new experiment'
            )
        from sim_platform.agents.configuration import run_universe

        universe = run_universe(config)
        if any(p['symbol'] not in universe for p in current['positions']):
            raise ContractError('agent experiments require positions in the selected symbols')
        descriptor = dict(
            name=config['definition']['name'], kind='agent', version=str(experiment.revision)
        )
        settings = dict(
            execution=copy_json(experiment.execution_config),
            research_source=copy_json(source),
            agent_experiment_id=experiment.id,
            agent=copy_json(config),
        )
        maker, run = self.runs.create_managed_run(
            profile_id,
            experiment.account_id,
            descriptor,
            config['symbol'] if len(universe) == 1 else universe,
            settings,
        )
        bound = copy_json(self.repo.runs.get(run).config)
        bound['agent']['run_id'] = run
        self.repo.runs.save_config(run, bound)
        return dict(maker_id=maker, run_id=run, experiment_id=experiment.id, config=bound['agent'])

    def create_agent_experiment_run(
        self,
        profile: str,
        account: str,
        agent_id: str,
        revision: int,
        settings: Dict[str, Any],
        source: Dict[str, Any],
        execution_config: Optional[ExecutionConfig] = None,
    ) -> AgentRunBinding:
        """Save configuration and create its run together in the caller's single UOW."""
        experiment = self.agents.prepare_agent_experiment(
            profile, account, agent_id, revision, settings, execution_config
        )
        return self.create_agent_run(profile, experiment['id'], source)
