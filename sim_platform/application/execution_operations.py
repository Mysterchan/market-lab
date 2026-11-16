"""Combine declared transaction use cases and runtime-aware financial operations.

This is an injected internal capability, not another public setup entrypoint.
Only ExecutionCoordinator holds/publishes runtime state; this adapter owns none.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple, cast

from .api_types import RunDetails

from .operations import DatabaseOperations, UnitOfWork

if TYPE_CHECKING:  # Annotation only.
    from sim_platform.execution.coordinator import ExecutionCoordinator
    from sim_platform.execution.models import Frontier
    from sim_platform.execution.state import ExecutionState
    from sim_platform.market.models import MarketSnapshot


class ApplicationOperations(DatabaseOperations):
    """Explicit application operations shared by controllers and runners.

    Transaction-only use cases retain DatabaseOperations implementations. Only the
    named financial/runtime-aware operations below go through ExecutionCoordinator.
    This keeps profile/Agent creation out of the financial coordinator and avoids
    arbitrary attribute forwarding. Platform remains the only composition root.
    """

    def __init__(self, storage: DatabaseOperations, execution: ExecutionCoordinator) -> None:
        self._storage, self._execution = storage, execution
        self.acquire_account = execution.acquire_account
        self.replay_source = execution.replay_source

    @property
    def _unit_of_work(self) -> UnitOfWork:  # type: ignore[override]
        # Not a bug: the base stores the factory in an attribute; this subclass reads it
        # from the database each time instead, which mypy reports as an override.
        return self._storage.unit_of_work

    def register_runtime(self, run_id: str, runtime: Optional[ExecutionState]) -> None:
        return self._execution.register_runtime(run_id, runtime)

    def unregister_runtime(self, run_id: str, runtime: Optional[ExecutionState]) -> None:
        return self._execution.unregister_runtime(run_id, runtime)

    def run(self, profile: str, run_id: str, *, include_records: bool = True) -> RunDetails:
        return cast(
            RunDetails, self._execution.run(profile, run_id, include_records=include_records)
        )

    def decision_journal(self, profile: str, run_id: str) -> Dict[str, Any]:
        return self._execution.decision_journal(profile, run_id)

    def decision_authority(self, profile: str, run_id: str) -> Tuple[str, str, int]:
        return self._execution.decision_authority(profile, run_id)

    def decision_summaries(
        self, profile: str, run_id: str, *, limit: int = 100
    ) -> List[Dict[str, Any]]:
        return self._execution.decision_summaries(profile, run_id, limit=limit)

    def run_events(
        self, profile: str, run_id: str, *, limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        return self._execution.run_events(profile, run_id, limit=limit)

    def execution(
        self,
        method: str,
        *args: Any,
        write: bool = False,
        runtime_state: Optional[Frontier] = None,
    ) -> Any:
        return self._execution.execution(method, *args, write=write, runtime_state=runtime_state)

    def direct_execution(
        self,
        method: str,
        profile: str,
        run_id: str,
        maker: str,
        value: Any,
        market: MarketSnapshot,
    ) -> Any:
        return self._execution.direct_execution(method, profile, run_id, maker, value, market)

    def checkpoint_execution(self, profile: str, run_id: str) -> None:
        return self._execution.checkpoint_execution(profile, run_id)

    def initialize_decisions(self, profile: str, run_id: str, maker: str) -> None:
        return self._execution.initialize_decisions(profile, run_id, maker)

    def commit_decision(
        self,
        profile: str,
        run_id: str,
        maker: str,
        market: MarketSnapshot,
        record: Optional[Dict[str, Any]] = None,
        expected_state: Any = None,
        *,
        action: Any = None,
        position: Optional[Dict[str, Any]] = None,
    ) -> Any:
        return self._execution.commit_decision(
            profile, run_id, maker, market, record, expected_state, action=action, position=position
        )

    def handoff(self, method: str, *args: Any) -> Any:
        return self._execution.handoff(method, *args)

    def managed_handoff(self, method: str, profile: str, run_id: str, *args: Any) -> Any:
        return self._execution.managed_handoff(method, profile, run_id, *args)

    def abort_execution_run(self, profile: str, run_id: str) -> Any:
        return self._execution.abort_execution_run(profile, run_id)
