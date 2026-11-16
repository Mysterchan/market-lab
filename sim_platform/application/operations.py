"""DB operation boundary shared by public Platform methods and downstream controllers."""

from __future__ import annotations

from dataclasses import asdict
from contextlib import contextmanager
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    ContextManager,
    Dict,
    Iterator,
    List,
    Optional,
    Tuple,
    Union,
    cast,
)

from sim_platform.domain.models import AccountSnapshot, ContractError, integer
from sim_platform.persistence.sql.repositories import Repositories
from sim_platform.application.services import ApplicationServices
from sim_platform.application.api_types import (
    AccountDetails,
    AgentExperimentDetails,
    AgentRevisionDetails,
    ArchiveReceipt,
    MakerDetails,
    ProfileDetails,
    ReportData,
    ReportStreams,
    ReportViewData,
    RunDetails,
)

if TYPE_CHECKING:  # Annotation only.
    from sim_platform.decisions.policy.contracts import Policy
    from sim_platform.decisions.policy.models import DecisionConfig
    from sim_platform.domain.models import Intent
    from sim_platform.execution import ExecutionConfig
    from sim_platform.execution.service import ExecutionService
    from sim_platform.persistence.agent_log_files import AgentLogArchive

# Opens one short transaction: unit_of_work(write=...) yields a SQLAlchemy Session
# (described as Any: application modules may not import sqlalchemy).
UnitOfWork = Callable[..., ContextManager[Any]]

# Default page size for Agent and experiment listings, and for decision summaries.
DEFAULT_PAGE_LIMIT = 100


class DatabaseOperations:
    """Every database read and write, each in its own short transaction.

    Platform creates one with the database's unit_of_work and injects it into
    controllers and runners; it holds no session between calls.
    """

    def __init__(self, unit_of_work: UnitOfWork) -> None:
        self._unit_of_work = unit_of_work

    @property
    def unit_of_work(self) -> UnitOfWork:
        """Borrow the injected short-transaction factory; no session is retained."""
        return self._unit_of_work

    @contextmanager
    def _services(self, *, write: bool = False) -> Iterator[ApplicationServices]:
        """Keep explicit use-case calls inside one committing/rolling-back transaction."""
        with self._unit_of_work(write=write) as session:
            yield ApplicationServices(Repositories(session))

    def validate_authority(self, profile_id: str, run_id: str, maker_id: str) -> None:
        """Validate a plain decision handle while its run is attached to the UOW."""
        with self._services() as services:
            services.runs.owned_run(profile_id, run_id).require_authority(maker_id)

    def create_profile(self, name: str) -> str:
        with self._services(write=True) as services:
            return services.accounts.create_profile(name)

    def profiles(self) -> List[ProfileDetails]:
        with self._services() as services:
            return cast(
                'List[ProfileDetails]',
                [asdict(profile) for profile in services.repo.profiles.all()],
            )

    def create_account(self, profile_id: str, name: str, cash: int, currency: str = "USD") -> str:
        with self._services(write=True) as services:
            return services.accounts.create_account(profile_id, name, cash, currency)

    def accounts(self, profile_id: str) -> List[AccountDetails]:
        with self._services() as services:
            services.repo.profiles.get(profile_id)
            return cast(
                'List[AccountDetails]',
                [asdict(value) for value in services.repo.accounts.for_profile(profile_id)],
            )

    def create_maker(
        self, profile_id: str, name: str, kind: str, version: str, config: Optional[Any] = None
    ) -> str:
        with self._services(write=True) as services:
            return services.makers.create_maker(profile_id, name, kind, version, config)

    def makers(self, profile_id: str) -> List[MakerDetails]:
        with self._services() as services:
            services.repo.profiles.get(profile_id)
            return cast(
                'List[MakerDetails]',
                [asdict(value) for value in services.repo.makers.for_profile(profile_id)],
            )

    def create_run(
        self,
        profile_id: str,
        account_id: str,
        maker_id: str,
        universe: List[str],
        config: Optional[Dict[str, Any]] = None,
    ) -> str:
        with self._services(write=True) as services:
            return services.runs.create_run(profile_id, account_id, maker_id, universe, config)

    def create_agent(self, profile_id: str, definition: Dict[str, Any]) -> AgentRevisionDetails:
        with self._services(write=True) as services:
            return cast(
                'AgentRevisionDetails', services.agents.create_agent(profile_id, definition)
            )

    def update_agent(
        self,
        profile_id: str,
        agent_id: str,
        expected_revision: int,
        definition: Dict[str, Any],
    ) -> AgentRevisionDetails:
        with self._services(write=True) as services:
            return cast(
                'AgentRevisionDetails',
                services.agents.update_agent(profile_id, agent_id, expected_revision, definition),
            )

    def agent(
        self, profile_id: str, agent_id: str, revision: Optional[int] = None
    ) -> AgentRevisionDetails:
        with self._services(write=False) as services:
            return cast(
                'AgentRevisionDetails', services.agents.agent(profile_id, agent_id, revision)
            )

    def agents(
        self, profile_id: str, limit: int = DEFAULT_PAGE_LIMIT, after_id: Optional[str] = None
    ) -> List[Any]:
        with self._services(write=False) as services:
            return services.agents.agents(profile_id, limit, after_id)

    def prepare_agent_experiment(
        self,
        profile_id: str,
        account_id: str,
        agent_id: str,
        revision: int,
        settings: Dict[str, Any],
        execution_config: Optional[ExecutionConfig] = None,
    ) -> AgentExperimentDetails:
        with self._services(write=True) as services:
            return cast(
                'AgentExperimentDetails',
                services.agents.prepare_agent_experiment(
                    profile_id, account_id, agent_id, revision, settings, execution_config
                ),
            )

    def agent_experiment(self, profile_id: str, experiment_id: str) -> AgentExperimentDetails:
        with self._services(write=False) as services:
            return cast(
                'AgentExperimentDetails',
                services.agents.agent_experiment(profile_id, experiment_id),
            )

    def create_agent_run(
        self, profile_id: str, experiment_id: str, source: Dict[str, Any]
    ) -> Dict[str, Any]:
        with self._services(write=True) as services:
            return services.agent_runs.create_agent_run(profile_id, experiment_id, source)

    def create_agent_experiment_run(
        self,
        profile: str,
        account: str,
        agent_id: str,
        revision: int,
        settings: Dict[str, Any],
        source: Dict[str, Any],
        execution_config: Optional[ExecutionConfig] = None,
    ) -> Dict[str, Any]:
        with self._services(write=True) as services:
            return services.agent_runs.create_agent_experiment_run(
                profile, account, agent_id, revision, settings, source, execution_config
            )

    def cancel_unbound_agent_run(self, profile_id: str, run_id: str) -> bool:
        with self._services(write=True) as services:
            return services.agent_runs.cancel_unbound_agent_run(profile_id, run_id)

    def agent_experiments(
        self, profile_id: str, limit: int = DEFAULT_PAGE_LIMIT, after_id: Optional[str] = None
    ) -> List[Any]:
        with self._services(write=False) as services:
            return services.agents.agent_experiments(profile_id, limit, after_id)

    def agent_log_header(self, profile_id: str, experiment_id: str, run_id: str) -> Dict[str, Any]:
        with self._services(write=False) as services:
            return services.agent_logs.agent_log_header(profile_id, experiment_id, run_id)

    def archive_agent_log(
        self, profile_id: str, run_id: str, archive: AgentLogArchive
    ) -> ArchiveReceipt:
        with self._services(write=True) as services:
            return cast(
                'ArchiveReceipt', services.agent_logs.archive_agent_log(profile_id, run_id, archive)
            )

    def agent_log_archive(self, profile_id: str, run_id: str) -> Optional[Any]:
        with self._services(write=False) as services:
            return services.agent_logs.agent_log_archive(profile_id, run_id)

    def runs(self, profile_id: str, *, include_records: bool = True) -> List[RunDetails]:
        with self._unit_of_work() as session:
            repositories = Repositories(session)
            repositories.profiles.get(profile_id)
            service = ApplicationServices(repositories)
            return cast(
                'List[RunDetails]',
                [
                    service.runs.run_details(profile_id, run.id, include_records)
                    for run in repositories.runs.for_profile(profile_id)
                ],
            )

    def run(self, profile_id: str, run_id: str, *, include_records: bool = True) -> RunDetails:
        with self._services(write=False) as services:
            return cast(
                'RunDetails', services.runs.run_details(profile_id, run_id, include_records)
            )

    def decision_records(
        self, profile_id: str, run_id: str, *, limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        if limit is not None:
            integer(limit, 'record limit', 1)
        with self._unit_of_work() as session:
            repo = Repositories(session)
            ApplicationServices(repo).owned_run(profile_id, run_id)
            return repo.runs.records(run_id, limit)

    def decision_summaries(
        self, profile_id: str, run_id: str, *, limit: int = DEFAULT_PAGE_LIMIT
    ) -> List[Dict[str, Any]]:
        integer(limit, 'record limit', 1)
        with self._unit_of_work() as session:
            repo = Repositories(session)
            ApplicationServices(repo).owned_run(profile_id, run_id)
            return repo.runs.record_summaries(run_id, limit)

    def run_events(
        self, profile_id: str, run_id: str, *, limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        if limit is not None:
            integer(limit, 'event limit', 1)
        with self._unit_of_work() as session:
            repo = Repositories(session)
            ApplicationServices(repo).owned_run(profile_id, run_id)
            return [asdict(e) for e in repo.events.for_run(run_id, limit=limit)]

    def archive_market(self, profile_id: str, run_id: str, archive: Any) -> None:
        with self._services(write=True) as services:
            return services.research.archive_market(profile_id, run_id, archive)

    @contextmanager
    def report_stream(
        self, profile_id: str, run_id: str
    ) -> Iterator[Tuple[ReportViewData, ReportStreams]]:
        """Iterators remain within one consistent read transaction until export finishes."""
        with self._unit_of_work() as session:
            services = ApplicationServices(Repositories(session))
            yield cast(
                'Tuple[ReportViewData, ReportStreams]',
                services.research.report_stream_data(profile_id, run_id),
            )

    def report_view_data(self, profile_id: str, run_id: str) -> ReportViewData:
        with self._services(write=False) as services:
            return cast('ReportViewData', services.research.report_view_data(profile_id, run_id))

    def report_data(self, profile_id: str, run_id: str) -> ReportData:
        with self._services(write=False) as services:
            return cast('ReportData', services.research.report_data(profile_id, run_id))

    def create_managed_decision_run(
        self,
        profile_id: str,
        account_id: str,
        symbol: Union[str, List[str]],
        policy: Policy,
        config: DecisionConfig,
        execution_config: ExecutionConfig,
        *,
        source: Optional[Dict[str, Any]] = None,
        allow_short: bool = False,
    ) -> Tuple[str, str]:
        """Create the maker and decision run together; return (maker_id, run_id).

        ``allow_short`` records the short-selling margin rule with the run.
        """
        from sim_platform.decisions.policy.contracts import policy_descriptor
        from sim_platform.decisions import DecisionConfig
        from sim_platform.execution import ExecutionConfig

        if not isinstance(config, DecisionConfig) or not isinstance(
            execution_config, ExecutionConfig
        ):
            raise ContractError('expected decision and execution configurations')
        descriptor = policy_descriptor(policy)
        from sim_platform.domain.json_values import copy_json

        settings = dict(
            execution=asdict(execution_config),
            decision=dict(config=asdict(config), policy=descriptor),
            research_source=copy_json(source),
        )
        if allow_short is True:
            from sim_platform.execution.risk import SHORT_SELLING_RULE

            # Saved only when allowed, so earlier runs keep their exact configuration.
            settings['short_selling'] = dict(SHORT_SELLING_RULE)
        elif allow_short is not False:
            raise ContractError('allow_short must be boolean')
        with self._services(write=True) as services:
            return services.runs.create_managed_run(
                profile_id, account_id, descriptor, symbol, settings
            )

    def finish_run(self, profile_id: str, run_id: str, status: str = "completed") -> None:
        with self._services(write=True) as services:
            return services.runs.finish_run(profile_id, run_id, status)

    def events(self, profile_id: str, account_id: str) -> List[Dict[str, Any]]:
        with self._services(write=False) as services:
            return services.accounts.events(profile_id, account_id)

    def snapshot(self, profile_id: str, account_id: str) -> AccountSnapshot:
        with self._services(write=False) as services:
            return services.accounts.snapshot(profile_id, account_id)

    def rebuild_account(self, profile_id: str, account_id: str) -> AccountSnapshot:
        """Rebuild the derived checkpoint from the complete, validated ledger."""
        with self._services(write=True) as services:
            return services.accounts.rebuild_account(profile_id, account_id)

    def replay_snapshot(
        self, profile_id: str, account_id: str, events: List[Dict[str, Any]]
    ) -> AccountSnapshot:
        with self._services(write=False) as services:
            return services.accounts.replay_snapshot(profile_id, account_id, events)

    def _submit(
        self, profile_id: str, run_id: str, maker_id: str, intent: Intent, time_ns: int
    ) -> str:
        with self._services(write=True) as services:
            return services.runs.submit(profile_id, run_id, maker_id, intent, time_ns)

    def _policy_state(self, profile_id: str, run_id: str, maker_id: str, value: Any) -> None:
        with self._services(write=True) as services:
            return services.runs.save_policy_state(profile_id, run_id, maker_id, value)

    def _fixture_event(
        self, profile_id: str, run_id: str, event_id: str, time_ns: int, kind: str, data: Any
    ) -> None:
        with self._services(write=True) as services:
            return services.runs.fixture_event(profile_id, run_id, event_id, time_ns, kind, data)

    def create_execution_run(
        self,
        profile_id: str,
        account_id: str,
        maker_id: str,
        symbol: str,
        config: Optional[ExecutionConfig] = None,
    ) -> str:
        from sim_platform.execution import ExecutionConfig

        config = ExecutionConfig() if config is None else config
        if not isinstance(config, ExecutionConfig):
            raise ContractError("expected ExecutionConfig")
        return self.create_run(
            profile_id, account_id, maker_id, [symbol], {"execution": asdict(config)}
        )

    def execution_data(self, profile: str, run_id: str) -> Any:
        from sim_platform.execution.service import ExecutionService

        with self._unit_of_work() as session:
            return ExecutionService(ApplicationServices(Repositories(session))).publication(
                profile, run_id
            )

    def create_decision_run(
        self,
        profile_id: str,
        account_id: str,
        maker_id: str,
        symbol: str,
        policy: Policy,
        config: Optional[DecisionConfig] = None,
        execution_config: Optional[ExecutionConfig] = None,
    ) -> str:
        from sim_platform.decisions.policy.models import CAPABILITIES, DecisionConfig
        from sim_platform.decisions.policy.contracts import policy_descriptor, default_config
        from sim_platform.execution import ExecutionConfig

        config = default_config(policy) if config is None else config
        execution_config = ExecutionConfig() if execution_config is None else execution_config
        if not isinstance(config, DecisionConfig) or not isinstance(
            execution_config, ExecutionConfig
        ):
            raise ContractError('expected decision and execution configurations')
        descriptor = policy_descriptor(policy)
        if any(c not in CAPABILITIES for c in descriptor['requires']):
            raise ContractError('required data capability is unavailable')
        return self.create_run(
            profile_id,
            account_id,
            maker_id,
            [symbol],
            {
                'execution': asdict(execution_config),
                'decision': {'config': asdict(config), 'policy': descriptor},
            },
        )

    def execution_read(self, method: str, *args: Any) -> Any:
        """Call a read method of ExecutionService in its own read transaction."""
        from sim_platform.execution.service import ExecutionService

        with self._unit_of_work() as session:
            return getattr(ExecutionService(ApplicationServices(Repositories(session))), method)(
                *args
            )

    def execution_change(
        self,
        profile: str,
        run_id: str,
        operation: Callable[[ExecutionService], Any],
        *,
        state: Any = None,
        previous: Any = None,
    ) -> Any:
        """Return a change only after the unit of work has committed successfully."""
        from sim_platform.execution.service import ExecutionService

        with self._unit_of_work(write=True) as session:
            service = ExecutionService(
                ApplicationServices(Repositories(session)), runtime_state=state, runtime=previous
            )
            result = operation(service)
            return service.complete_change(profile, run_id, result, previous=previous)
