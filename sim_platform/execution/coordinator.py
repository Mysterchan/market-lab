"""Execution coordination over injected transactions and owner registry; no SQL."""

from __future__ import annotations

from contextlib import nullcontext
import logging
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Tuple

from sim_platform.domain.json_values import copy_json
from sim_platform.domain.models import ContractError, integer

from .models import Frontier

_log = logging.getLogger(__name__)

if TYPE_CHECKING:  # Annotation only: no runtime import of the market package or state owner.
    from sim_platform.market.models import MarketSnapshot

    from .state import ExecutionState

# Read requests up to this size are answered from the running owner's memory.
RUNTIME_HISTORY_LIMIT = 100


class ExecutionCoordinator:
    """Route every financial read and write for a run through one place.

    A run that is executing has an owner holding its state in memory (ExecutionState).
    For such a run the coordinator serializes each change with the owner's lock, runs
    it as one database transaction, and installs the committed result in the owner's
    memory afterwards. Small reads are answered from that memory; larger reads, and
    runs with no resident owner, go to the database.

    Args:
        operations: Opens transactions (`execution_change`) and plain reads.
        registry: Tracks which runs have a resident owner.
        source_identity: Describes a market source for a later recorded replay.
        lease_factory: Acquires exclusive write access to an account.
        lineage: Optional ``lineage(event, profile, run_id)`` called after a run binds
            ('bind') or ends ('finish'); its failures are logged, never raised.
    """

    def __init__(
        self,
        operations: Any,
        registry: Any,
        *,
        source_identity: Callable[..., Any],
        lease_factory: Callable[[str], Any],
        lineage: Optional[Callable[[str, str, str], None]] = None,
    ) -> None:
        self.lineage = lineage
        self.database = operations
        self.registry = registry
        self.replay_source = source_identity
        self.acquire_account = lease_factory

    def register_runtime(self, run_id: str, runtime: Optional[ExecutionState]) -> None:
        self.registry.register_runtime(run_id, runtime)

    def unregister_runtime(self, run_id: str, runtime: Optional[ExecutionState]) -> None:
        self.registry.unregister_runtime(run_id, runtime)

    def _write(
        self,
        profile: str,
        run_id: str,
        operation: Callable[[Any], Any],
        *,
        state: Optional[Frontier] = None,
    ) -> Any:
        """Run ``operation`` in one transaction, then publish its result to the owner.

        ``operation`` receives the transaction's ExecutionService. ``state`` is the
        frontier to save with the change; by default the owner supplies the next one.
        The owner's memory is updated only after the transaction has committed.
        """
        runtime = self.registry.runtime(run_id)
        with runtime.lock if runtime else nullcontext():
            previous = runtime.publication() if runtime else None
            proposed = (
                state if state is not None else runtime.operation_state() if runtime else None
            )
            change = self.database.execution_change(
                profile, run_id, operation, state=proposed, previous=previous
            )
            if runtime:
                runtime.update(change.publication)
            return change.result

    def run(self, profile: str, run_id: str, *, include_records: bool = True) -> Dict[str, Any]:
        """Return the run's details; without records, a resident owner answers from memory."""
        runtime = self.registry.runtime(run_id)
        if runtime is not None and runtime.profile == profile and not include_records:
            with runtime.lock:
                value = copy_json(runtime.run)
                value['time_ns'] = runtime.last['market']['time_ns']
                return value
        return self.database.run(profile, run_id, include_records=include_records)

    def decision_journal(self, profile: str, run_id: str) -> Dict[str, Any]:
        """Return the run's decision journal (its saved policy state)."""
        runtime = self.registry.runtime(run_id)
        if runtime is not None and runtime.profile == profile:
            with runtime.lock:
                return copy_json(runtime.run['policy_state'])
        return self.database.run(profile, run_id, include_records=False)['policy_state']

    def decision_authority(self, profile: str, run_id: str) -> Tuple[str, str, int]:
        """Return (run status, current decision maker ID, handoff generation)."""
        runtime = self.registry.runtime(run_id)
        with runtime.lock if runtime else nullcontext():
            run = (
                runtime.run
                if runtime and runtime.profile == profile
                else self.database.run(profile, run_id, include_records=False)
            )
            return run['status'], run['maker_id'], run['policy_state'].get('generation', 0)

    def decision_summaries(
        self, profile: str, run_id: str, *, limit: int = RUNTIME_HISTORY_LIMIT
    ) -> List[Dict[str, Any]]:
        """Return the most recent decision summaries, newest last."""
        integer(limit, 'record limit', 1)
        runtime = self.registry.runtime(run_id)
        if runtime is not None and runtime.profile == profile and limit <= RUNTIME_HISTORY_LIMIT:
            with runtime.lock:
                return copy_json(runtime.decisions[-limit:])
        return self.database.decision_summaries(profile, run_id, limit=limit)

    def run_events(
        self, profile: str, run_id: str, *, limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """Return the run's ledger events; ``limit`` keeps only the most recent ones."""
        if limit is not None:
            integer(limit, 'event limit', 1)
        runtime = self.registry.runtime(run_id)
        if (
            runtime is not None
            and runtime.profile == profile
            and limit is not None
            and limit <= RUNTIME_HISTORY_LIMIT
        ):
            with runtime.lock:
                return copy_json(runtime.events[-limit:])
        return self.database.run_events(profile, run_id, limit=limit)

    def execution(
        self,
        method: str,
        *args: Any,
        write: bool = False,
        runtime_state: Optional[Frontier] = None,
    ) -> Any:
        """Call the named ExecutionService method; ``args`` start with profile and run ID.

        ``write=True`` runs it as a financial change. A snapshot of a run with a
        resident owner is answered from memory.
        """
        profile, run_id = args[:2]
        runtime = self.registry.runtime(run_id)
        if runtime and method == 'snapshot':
            if runtime.profile != profile:
                raise ContractError('profile does not own this object')
            with runtime.lock:
                return runtime.snapshot
        if write:
            result = self._write(
                profile,
                run_id,
                lambda service: getattr(service, method)(*args),
                state=runtime_state,
            )
            if self.lineage is not None and method in ('bind', 'finish'):
                try:
                    self.lineage(method, profile, run_id)
                except Exception:
                    _log.warning('Could not record lineage of run %s', run_id, exc_info=True)
            return result
        return self.database.execution_read(method, *args)

    def direct_execution(
        self,
        method: str,
        profile: str,
        run_id: str,
        maker: str,
        value: Any,
        market: MarketSnapshot,
    ) -> Any:
        """Submit or cancel outside a decision runner.

        The service returns a rejection instead of raising, so that a run which journals
        its operations can commit the failed attempt; the rejection is raised here.
        """
        result = self._write(
            profile,
            run_id,
            lambda service: service.direct_operation(method, profile, run_id, maker, value, market),
        )
        if isinstance(result, ContractError):
            raise result
        return result

    def checkpoint_execution(self, profile: str, run_id: str) -> None:
        """Save a resident owner's current position; a run without one has nothing to save."""
        runtime = self.registry.runtime(run_id)
        if runtime:
            self._write(profile, run_id, lambda service: None, state=runtime.last)

    def initialize_decisions(self, profile: str, run_id: str, maker: str) -> None:
        """Create the run's decision journal for its first decision maker."""
        from sim_platform.decisions.service import initialize_decisions

        runtime = self.registry.runtime(run_id)
        self._write(
            profile,
            run_id,
            lambda service: initialize_decisions(service, profile, run_id, maker),
            state=runtime.last if runtime else None,
        )

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
        """Shared policy/tool financial submission; tools do not write policy audit."""
        from sim_platform.decisions.service import commit

        return self._write(
            profile,
            run_id,
            lambda service: commit(
                service,
                profile,
                run_id,
                maker,
                market,
                record,
                expected_state,
                action=action,
                position=position,
            ),
        )

    def handoff(self, method: str, *args: Any) -> Any:
        """Run the named policy-handoff function; ``args`` start with profile and run ID."""
        from sim_platform.decisions.policy import handoffs

        return self._write(
            args[0], args[1], lambda service: getattr(handoffs, method)(service, *args)
        )

    def managed_handoff(self, method: str, profile: str, run_id: str, *args: Any) -> Any:
        """Run a handoff requested through the managed decision service."""
        from sim_platform.decisions.service import managed_handoff

        return self._write(
            profile,
            run_id,
            lambda service: managed_handoff(service, method, profile, run_id, *args),
        )

    def abort_execution_run(self, profile: str, run_id: str) -> Any:
        """Stop a run that has no live owner, for example after an interrupted session."""
        if self.registry.has_execution(run_id):
            raise ContractError('close the active execution owner instead')
        run = self.run(profile, run_id, include_records=False)
        lease = self.acquire_account(run['account_id'])
        try:
            return self.execution(
                'finish',
                profile,
                run_id,
                'stopped',
                run['time_ns'],
                'explicit recovery stop',
                write=True,
            )
        finally:
            lease.close()
