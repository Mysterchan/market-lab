"""One execution owner drives one MarketSession and one persisted account run."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict
from threading import RLock
from typing import TYPE_CHECKING, Any, Callable, Dict, Iterator, Optional, Tuple

from sim_platform.domain.json_values import copy_json
from sim_platform.domain.models import ContractError, Intent, integer
from sim_platform.resources import cleanup, cleanup_after_error, serialized

from .engine import snapshot_universe
from .models import ExecutionConfig, ExecutionSnapshot, Fill, Frontier
from .state import ExecutionState

if TYPE_CHECKING:  # Annotation only: no runtime import of the market package.
    from sim_platform.market.models import MarketEvent, MarketSnapshot


def _require_startable(
    operations: Any,
    ownership: Any,
    run: Dict[str, Any],
    profile: str,
    maker: str,
    market: Any,
) -> None:
    """Reject a run or market that cannot start a new execution session.

    The run must be active, owned by ``maker``, configured for execution on this one
    symbol, and have no ledger history. The market must be either untouched or exactly
    at the start point saved when the experiment was prepared. Neither may already
    have an execution owner.
    """
    if run['status'] != 'running' or run['maker_id'] != maker:
        raise ContractError('run is inactive or maker has no authority')
    if 'execution' not in run['config']:
        raise ContractError('run is not configured for execution')
    ExecutionConfig(**run['config']['execution'])  # Validates the saved settings.
    # The run stores its universe sorted; the merged market keeps the chosen order.
    if sorted(run['universe']) != sorted(snapshot_universe(market.snapshot)):
        raise ContractError('execution requires the matching market symbols')
    source = run['config'].get('research_source') or {}
    origin = source.get('start_origin')
    prepared = origin == dict(
        sequence=market.snapshot.sequence, time_ns=market.time_ns
    ) and market.status in ('ready', 'paused')
    untouched = market.status == 'ready' and market.time_ns == 0 and market.snapshot.sequence == 0
    if not prepared and not untouched:
        raise ContractError('bind a fresh ready market session')
    if operations.run_events(profile, run['id'], limit=1):
        raise ContractError('execution restart is unsupported; replay ledger or create a new run')
    if ownership.has_execution(run['id']) or market.has_execution_owner:
        raise ContractError('run or market already has an execution owner')


class ExecutionSession:
    """The single owner that drives one market session for one account run.

    Creating a session takes exclusive write access to the account, claims the run
    and the market, and records the starting position. From then on every market step
    goes through this object, so the market and the account ledger cannot drift apart.
    If the market is advanced by anyone else, the next call fails the run.

    Ending the run (``finish``, ``fail``, ``close``) or a processing error releases
    all three claims. A session is not restartable: a finished run needs a new run.

    Args:
        operations: Financial reads and writes (ExecutionCoordinator through Platform).
        profile: Profile that owns the run.
        run_id: The run to execute; it must be active and have no history.
        maker: Decision maker that currently holds authority for the run.
        market: The market session to drive.
        ownership: Registry that allows one execution owner per run.
    """

    def __init__(
        self,
        operations: Any,
        profile: str,
        run_id: str,
        maker: str,
        market: Any,
        *,
        ownership: Any,
    ) -> None:
        run = operations.run(profile, run_id)
        _require_startable(operations, ownership, run, profile, maker, market)
        self._lease = operations.acquire_account(run['account_id'])
        try:
            ownership.claim_execution(run_id, self)
        except Exception:
            self._lease.close()
            raise
        try:
            market.claim_execution(self)
            operations.execution(
                'bind',
                profile,
                run_id,
                maker,
                market.snapshot,
                operations.replay_source(market.source_descriptor()),
                write=True,
            )
        except Exception:
            market.release_execution(self)
            ownership.release_execution(run_id, self)
            self._lease.close()
            raise
        # Runs with this storage mode keep their financial state in memory between
        # changes; older runs read and write the database on every market event.
        self._runtime: Optional[ExecutionState] = None
        try:
            if run['config'].get('storage') == 'runtime-v1':
                self._runtime = ExecutionState(operations, profile, run_id)
                operations.register_runtime(run_id, self._runtime)
        except Exception:
            market.release_execution(self)
            ownership.release_execution(run_id, self)
            self._lease.close()
            raise
        self._control_lock = self._runtime.lock if self._runtime else RLock()
        self._ownership = ownership
        self._on_close: Callable[[], None] = lambda: None
        self._operations, self._profile, self._run, self._maker = operations, profile, run_id, maker
        self._market = market
        self._closed = False
        # True while a market callback is running; controls are refused during it.
        self._processing = False
        self._fills: Tuple[Fill, ...] = ()
        # The market position this session last processed.
        self._sequence, self._time = market.snapshot.sequence, market.time_ns

    @contextmanager
    def control_boundary(self) -> Iterator[None]:
        """Serialize observation and authority checks with the resulting operation."""
        with self._control_lock:
            yield

    def on_close(self, callback: Callable[[], None]) -> None:
        """Register one callback to run when this session releases its ownership."""
        self._on_close = callback

    @property
    @serialized
    def snapshot(self) -> ExecutionSnapshot:
        """The account with current reservations, at the last processed market position."""
        if self._runtime:
            return self._runtime.snapshot
        return self._operations.execution('snapshot', self._profile, self._run)

    @property
    def market_snapshot(self) -> MarketSnapshot:
        return self._market.snapshot

    @property
    def last_fills(self) -> Tuple[Fill, ...]:
        """Fills produced by the most recent market event; empty if there were none."""
        return self._fills

    @property
    @serialized
    def account_sequence(self) -> int:
        """Committed account version without projecting a full execution view."""
        if self._runtime is not None:
            return self._runtime.account.sequence
        return self.snapshot.account.sequence

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    @serialized
    def replay_state(self) -> Frontier:
        """A detached copy of the financial frontier, for verifying a recorded replay."""
        if self._runtime is None:
            raise ContractError('recorded replay requires runtime-v1')
        return copy_json(self._runtime.last)

    @serialized
    def finish_recorded_replay(self, status: str, reason: str) -> ExecutionSnapshot:
        return self._finish(status, reason)

    def validate_control(self) -> None:
        """Check lifecycle, callback reentrancy and synchronization before a decision."""
        self._check()

    @serialized
    def adopt_maker(self, maker: str) -> None:
        """Follow an authority change only after the database has committed it."""
        self._check()
        run = self._operations.run(self._profile, self._run, include_records=False)
        if run['status'] != 'running' or run['maker_id'] != maker:
            raise ContractError('maker has no committed instruction authority')
        self._maker = maker

    def fail(self, reason: str) -> ExecutionSnapshot:
        """Terminate a failed decision run and release its execution ownership."""
        self._check()
        return self._finish('failed', reason)

    def _check(self) -> None:
        """Refuse a closed session, a reentrant call, or a market moved by someone else.

        A market that advanced outside this session can no longer be reconciled with
        the ledger, so the run is failed and the market closed before raising.
        """
        if self._closed:
            raise ContractError('execution session is closed')
        if self._processing or self._market.is_notifying:
            raise ContractError('execution controls cannot run inside market callbacks')
        if self._market.snapshot.sequence != self._sequence or self._market.time_ns != self._time:
            try:
                self._finish('failed', 'market was advanced outside the execution session')
            finally:
                cleanup(self._market.close, self._unbind)
            raise ContractError('market was advanced outside the execution session')

    @serialized
    def submit(self, intent: Intent) -> Any:
        """Submit an order directly; runs with a decision policy must use their runner."""
        self._check()
        if (
            'decision'
            in self._operations.run(self._profile, self._run, include_records=False)['config']
        ):
            raise ContractError('submit through DecisionRunner to record observation and action')
        return self._operations.direct_execution(
            'submit', self._profile, self._run, self._maker, intent, self._market.snapshot
        )

    @serialized
    def cancel(self, order_id: str) -> Any:
        """Cancel an order directly; runs with a decision policy must use their runner."""
        self._check()
        if (
            'decision'
            in self._operations.run(self._profile, self._run, include_records=False)['config']
        ):
            raise ContractError('cancel through DecisionRunner to record observation and action')
        return self._operations.direct_execution(
            'cancel', self._profile, self._run, self._maker, order_id, self._market.snapshot
        )

    @serialized
    def abandon(self) -> None:
        """Release execution after an untrusted storage failure, without more SQL.

        The lifecycle owner must stop driving the market. Committed orders and
        the unresolved DB run are preserved for explicit recovery.
        """
        if not self._closed:
            self._unbind()

    @serialized
    def play(self) -> Any:
        self._check()
        return self._market.play()

    @serialized
    def pause(self) -> Any:
        """Pause the market and save the current financial position."""
        self._check()
        result = self._market.pause()
        self._operations.checkpoint_execution(self._profile, self._run)
        return result

    @serialized
    def step(self) -> MarketEvent:
        """Advance a ready or paused market by exactly one event and process it."""
        self._check()
        if self._market.status not in ('ready', 'paused'):
            raise ContractError('step requires ready or paused market')
        return self._advance(self._market.step)

    @serialized
    def pump(self, count: int = 1) -> Tuple[MarketEvent, ...]:
        """Process up to ``count`` events while the market is playing.

        Stops early when the market stops playing or the run ends.
        """
        self._check()
        if type(count) is not int or count < 1:
            raise ContractError('pump count must be a positive integer')
        events = []
        for _ in range(count):
            if self._closed or self._market.status != 'playing':
                break
            events.append(self._advance(lambda: self._market.pump(1)[0]))
        return tuple(events)

    def _advance(self, advance: Callable[[], MarketEvent]) -> MarketEvent:
        """Take one event from the market and apply it to the account.

        A market that completes or fails ends the run. Any error here leaves the
        market ahead of the ledger, so the run is failed and everything released.
        """
        self._processing = True
        try:
            self._fills = ()
            event = advance()
            if event.kind in ('market', 'clock', 'news'):
                self._fills = (
                    self._runtime.advance(event)
                    if self._runtime
                    else self._operations.execution(
                        'advance', self._profile, self._run, event, write=True
                    )
                )
                self._sequence, self._time = event.sequence, event.time_ns
            elif event.snapshot.status in ('completed', 'failed'):
                status = event.snapshot.status
                self._finish(status, event.message or f'market {status}')
            return event
        except Exception as exc:
            # Native state cannot be rolled back after a database failure. Never continue.
            cleanup_after_error(
                exc,
                lambda: self._finish('failed', 'execution processing failed'),
                self._market.close,
                self._unbind,
            )
            raise
        finally:
            self._processing = False

    @serialized
    def advance_idle_prefix(self, end_prefix: int) -> Tuple[int, Optional[MarketEvent]]:
        """Batch only immutable, no-fill source transitions on the same owner.

        No financial submission/commit path is bypassed. A possible fill,
        publication or observer requires ordinary per-event execution.
        """
        self._check()
        if self._runtime is None:
            return 0, None
        event = self._market.idle_candidate(end_prefix)
        if event is None or not self._runtime.can_advance_idle(event.snapshot):
            return 0, None
        count = end_prefix - self._market.merged_prefix
        if event.sequence != self._sequence + count:
            raise ContractError('idle span crosses a non-source publication')
        try:
            self._runtime.advance_idle(event, count)
        except Exception:
            self.abandon()
            raise
        self._market.apply_idle(end_prefix, event)
        self._sequence, self._time = event.sequence, event.time_ns
        self._fills = ()
        return count, event

    def _finish(self, status: str, reason: str, time_ns: Optional[int] = None) -> ExecutionSnapshot:
        """Record the end of the run, then release ownership even if recording fails."""
        if self._runtime:
            snapshot = asdict(self._market.snapshot)
            if snapshot['sequence'] != self._runtime.last['market_sequence']:
                snapshot = {**self._runtime.last['market'], 'status': 'failed', 'error': reason}
            self._runtime.last = {**self._runtime.last, 'market': snapshot}
        try:
            return self._operations.execution(
                'finish',
                self._profile,
                self._run,
                status,
                self._market.time_ns if time_ns is None else time_ns,
                reason,
                write=True,
            )
        finally:
            self._unbind()

    def _unbind(self) -> None:
        """Release the run, the account lease and the market; the session is then closed."""
        self._closed = True
        self._ownership.release_execution(self._run, self)
        self._operations.unregister_runtime(self._run, self._runtime)
        self._lease.close()
        self._on_close()
        self._market.release_execution(self)

    @serialized
    def finish(
        self, status: str, reason: str, *, time_ns: Optional[int] = None
    ) -> ExecutionSnapshot:
        """End at an owner clock boundary without consuming the end-time source frame."""
        self._check()
        if time_ns is not None:
            integer(time_ns, 'execution end time')
            if time_ns < self._market.time_ns:
                raise ContractError('execution end precedes the source prefix')
        return self._finish(status, reason, time_ns)

    @serialized
    def close(self) -> None:
        """Stop the run if it is still active; closing twice is harmless."""
        if not self._closed:
            self._check()
            cleanup(lambda: self._finish('stopped', 'execution stopped by user'), self._unbind)

    def __enter__(self) -> ExecutionSession:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
