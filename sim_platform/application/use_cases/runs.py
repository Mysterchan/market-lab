"""RunService operations within one caller-owned transaction."""

from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple, Union

from sim_platform.domain.models import (
    ContractError,
    DecisionMaker,
    Intent,
    Run,
    integer,
    label,
)
from sim_platform.domain.json_values import copy_json
from sim_platform.application.identifiers import identifier

if TYPE_CHECKING:  # Annotation only.
    from .accounts import AccountService
    from .makers import MakerService

# The unit of work's persistence Repositories; use cases may not import persistence.
Repositories = Any


class RunService:
    """Run creation, ownership, ledger events and completion inside the caller's unit of work."""

    def __init__(
        self, repositories: Repositories, accounts: AccountService, makers: MakerService
    ) -> None:
        self.repo, self.accounts, self.makers = repositories, accounts, makers

    def create_run(
        self,
        profile_id: str,
        account_id: str,
        maker_id: str,
        universe: List[str],
        config: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Create a running run for the account and maker; return its ID."""
        if (
            not isinstance(universe, list)
            or not universe
            or not all(isinstance(s, str) for s in universe)
        ):
            raise ContractError("universe must be a nonempty list of symbols")
        if len(set(universe)) != len(universe):
            raise ContractError("universe must be a nonempty list of distinct symbols")
        for symbol in universe:
            Intent("validation", symbol, "buy", 1, 1).validate()
        self.repo.accounts.owned(account_id, profile_id)
        maker = self.repo.makers.owned(maker_id, profile_id)
        settings = copy_json({} if config is None else config)
        if isinstance(settings, dict) and 'execution' in settings:
            settings.setdefault('storage', 'runtime-v1')
        self._validate_run_config(settings, maker, universe)
        if self.repo.runs.active_for_account(account_id):
            raise ContractError("account already has an active run")
        snapshot = self.accounts.snapshot(profile_id, account_id)
        if any(order.status in ("accepted", "partial") for order in snapshot.orders):
            raise ContractError("resolve existing orders before creating another run")
        run = Run(
            identifier(),
            account_id,
            maker_id,
            "running",
            0,
            sorted(universe),
            settings,
            asdict(snapshot),
            {},
        )
        self.repo.runs.add(run)
        return run.id

    @staticmethod
    def _validate_run_config(config: Any, maker: DecisionMaker, universe: List[str]) -> None:
        if not isinstance(config, dict):
            raise ContractError('run configuration must be an object')
        try:
            if 'execution' in config:
                from sim_platform.execution.models import ExecutionConfig

                ExecutionConfig(**config['execution'])
                # Several symbols need a prepared research source that merges them.
                if len(universe) != 1 and not (config.get('research_source') or {}).get('universe'):
                    raise ContractError('execution requires a single market symbol')
            if 'decision' in config:
                from sim_platform.decisions.policy.models import DecisionConfig, CAPABILITIES

                if 'execution' not in config:
                    raise ContractError('decision run requires execution configuration')
                DecisionConfig(**config['decision']['config'])
                descriptor = config['decision']['policy']
                if descriptor['kind'] != maker.kind:
                    raise ContractError('policy kind differs from registered maker')
                label(descriptor['name'], 'policy name')
                label(descriptor['version'], 'policy version')
                requires = descriptor['requires']
                if not isinstance(requires, list) or any(c not in CAPABILITIES for c in requires):
                    raise ContractError('required data capability is unavailable')
        except (TypeError, KeyError) as exc:
            raise ContractError('invalid execution or decision run configuration') from exc

    def create_managed_run(
        self,
        profile_id: str,
        account_id: str,
        descriptor: Dict[str, Any],
        symbol: Union[str, List[str]],
        config: Dict[str, Any],
    ) -> Tuple[str, str]:
        """Create a frontend-managed maker/run together, or neither on failure.

        ``symbol`` is the run's one symbol, or its universe as a list of symbols.
        """
        self.repo.accounts.owned(account_id, profile_id)
        maker = self.makers.create_maker(
            profile_id, descriptor['name'], descriptor['kind'], descriptor['version']
        )
        universe = [symbol] if isinstance(symbol, str) else list(symbol)
        return maker, self.create_run(profile_id, account_id, maker, universe, config)

    def owned_run(self, profile_id: str, run_id: str) -> Run:
        """The run; raise unless its account belongs to the profile."""
        run: Run = self.repo.runs.get(run_id)
        self.repo.accounts.owned(run.account_id, profile_id)
        return run

    def run_details(
        self, profile_id: str, run_id: str, include_records: bool = True
    ) -> Dict[str, Any]:
        """The run as a dictionary with its policy state and decision maker."""
        run = self.owned_run(profile_id, run_id)
        return {
            **asdict(run),
            'policy_state': self.repo.runs.journal(run_id, include_records=include_records),
            "decision_maker": asdict(self.repo.makers.get(run.maker_id)),
        }

    def finish_run(self, profile_id: str, run_id: str, status: str = "completed") -> None:
        if status not in ("completed", "stopped", "failed"):
            raise ContractError("invalid terminal run status")
        run = self.owned_run(profile_id, run_id)
        run.require_active()
        if isinstance(run.config, dict) and "execution" in run.config:
            ending = self.repo.events.latest(run_id, 'execution_end')
            if not ending or ending.data["status"] != status:
                raise ContractError("finish execution through ExecutionSession")
        state = self.repo.checkpoints.get(run.account_id)
        if status == "completed":
            if state.has_open_orders():
                raise ContractError("cannot complete a run with open fixture orders")
            if state.has_pending_intents(run_id):
                raise ContractError("cannot complete a run with unresolved intents")
        self.repo.runs.save_final(
            run_id, asdict(self.accounts.snapshot(profile_id, run.account_id))
        )
        self.repo.runs.set_status(run_id, status)

    def record_run_event(self, run: Run, event_id: str, time_ns: int, kind: str, data: Any) -> None:
        integer(time_ns, "simulation time")
        old = self.repo.events.find(run.account_id, event_id)
        if not old and time_ns < run.time_ns:
            raise ContractError("simulation time moved backwards")
        self.accounts.append_event(run.account_id, event_id, run.id, time_ns, kind, data)
        self.repo.runs.advance_time(run.id, time_ns)

    def submit(
        self, profile_id: str, run_id: str, maker_id: str, intent: Intent, time_ns: int
    ) -> str:
        """Record an intent event for a fixture (non-execution) run; return the intent ID."""
        if not isinstance(intent, Intent):
            raise ContractError("expected Intent")
        intent.validate()
        integer(time_ns, "simulation time")
        run = self.owned_run(profile_id, run_id)
        if isinstance(run.config, dict) and "execution" in run.config:
            raise ContractError(
                "submit through ExecutionSession for risk and simulation time checks"
            )
        run.require_authority(maker_id)
        run.require_symbol(intent.symbol)
        intent_id = f"{run_id}:intent:{intent.client_id}"
        data = {
            "intent_id": intent_id,
            "run_id": run_id,
            "maker_id": maker_id,
            "intent": asdict(intent),
        }
        self.record_run_event(run, intent_id, time_ns, "intent", data)
        return intent_id

    def save_policy_state(self, profile_id: str, run_id: str, maker_id: str, value: Any) -> None:
        run = self.owned_run(profile_id, run_id)
        run.require_authority(maker_id)
        if isinstance(run.config, dict) and 'decision' in run.config:
            raise ContractError('decision runner owns the policy state and audit')
        self.repo.runs.save_policy_state(run_id, copy_json(value))

    def fixture_event(
        self, profile_id: str, run_id: str, event_id: str, time_ns: int, kind: str, data: Any
    ) -> None:
        run = self.owned_run(profile_id, run_id)
        if isinstance(run.config, dict) and "execution" in run.config:
            raise ContractError("fixture events are forbidden in execution runs")
        run.require_active()
        self.record_run_event(run, event_id, time_ns, kind, data)
