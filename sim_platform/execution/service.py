"""Transaction-bound execution rules and detached results; risk, OMS and fills are atomic."""

from __future__ import annotations

from dataclasses import asdict, replace
from hashlib import sha256
from typing import TYPE_CHECKING, Any, Dict, Optional, Sequence, Tuple

from sim_platform.domain.json_values import copy_json
from sim_platform.domain.models import (
    AccountSnapshot,
    ContractError,
    Intent,
    MarketOrder,
    OrderSnapshot,
    Run,
    integer,
    label,
)

from .engine import advance, snapshot_universe, start_books
from .liquidity import refresh
from .costs import order_delay
from .models import (
    ExecutionConfig,
    ExecutionContext,
    ExecutionSnapshot,
    Fill,
    Frontier,
    OrderResult,
)
from .position import position, validate_agent_position
from .publication import accepted_orders, build_publication
from .risk import MARKET_PROTECTION_BPS, OPEN, protection_price, rejection_reason, reservations
from .state import ExecutionChange, RuntimeData

if TYPE_CHECKING:  # Annotation only: no runtime import of the market package.
    from sim_platform.market.models import MarketEvent, MarketSnapshot

    from .state import ExecutionState

# A run's journal of direct submit/cancel attempts stops growing at this size.
OPERATION_JOURNAL_LIMIT = 10000


def _journals_state(run: Run) -> bool:
    """Whether the run keeps its frontier in a saved state row instead of tick events.

    Runs created with this storage mode also stamp each event with its operation
    position, which a recorded replay later uses to reproduce the same order.
    """
    return run.config.get('storage') == 'runtime-v1'


class ExecutionService:
    """Order, fill and run-ending rules for one database transaction.

    A new service is created for every unit of work and discarded afterwards, so its
    caches never outlive the transaction. Each public method validates authority and
    market synchronization, then records ledger events through ``accounts``. Nothing
    here commits: the caller owns the transaction, and ExecutionCoordinator publishes
    the result to the run's owner after it commits.

    Args:
        accounts: The transaction's account/run services and their repositories.
        runtime_state: The frontier proposed by the run's owner for this change.
        runtime: The owner's in-memory state, when the run has a resident owner.
    """

    def __init__(
        self,
        accounts: Any,
        *,
        runtime_state: Optional[Frontier] = None,
        runtime: Optional[ExecutionState] = None,
    ) -> None:
        self.accounts = accounts
        self.repo = accounts.repo
        self.runtime_state = runtime_state
        self._context_run: Optional[Run] = None
        self._account_cache: Dict[Tuple[int, bool], AccountSnapshot] = {}
        self.runtime = runtime
        # Set by any ledger write; the next account read must then come from storage.
        self._account_dirty = False
        self._agent_position: Optional[Dict[str, Any]] = None

    def _load_run(self, profile: str, run_id: str) -> Run:
        """Reuse the transaction's run, or the owner's already committed run values."""
        if self._context_run is None:
            if self.runtime is not None and self.runtime.profile == profile:
                self._context_run = Run(
                    **{key: self.runtime.run[key] for key in Run.__dataclass_fields__}
                )
            else:
                self._context_run = self.accounts.owned_run(profile, run_id)
        return self._context_run

    def _load_account(self, profile: str, run: Run, *, include_closed: bool) -> AccountSnapshot:
        """Use RAM until a ledger write requires a refreshed account snapshot."""
        if self.runtime is not None and not self._account_dirty:
            return self.runtime.account
        sequence = self.repo.checkpoints.get(run.account_id).sequence
        key = (sequence, include_closed)
        if key not in self._account_cache:
            self._account_cache[key] = self.accounts.snapshot(
                profile, run.account_id, include_closed=include_closed
            )
        return self._account_cache[key]

    def _load_execution_state(self, run: Run) -> Frontier:
        """Prefer the proposed owner state; recover stored state for nonresident calls."""
        last = self.runtime_state or self.repo.runs.execution_state(run.id)
        if last is None:
            tick = self.repo.events.latest(run.id, 'execution_tick')
            last = (
                copy_json(tick.data) if tick else dict(tick=0, market_sequence=0, bids=[], asks=[])
            )
        if _journals_state(run) and last['tick']:
            self.runtime_state = last
        return last

    def context(
        self, profile: str, run_id: str, *, include_closed: bool = False
    ) -> ExecutionContext:
        """Load run, settings, account and financial frontier in the current transaction.

        A Service lives for one unit of work. Its caches never outlive that transaction;
        writes invalidate account reuse through ``_account_dirty``.
        """
        run = self._load_run(profile, run_id)
        if not isinstance(run.config, dict) or 'execution' not in run.config:
            raise ContractError('run is not configured for execution')
        config = ExecutionConfig(**run.config['execution'])
        account = self._load_account(profile, run, include_closed=include_closed)
        last = self._load_execution_state(run)
        if self.runtime_state:
            run = replace(run, time_ns=last['market']['time_ns'])
        return ExecutionContext(run, config, account, last)

    def snapshot(self, profile: str, run_id: str) -> ExecutionSnapshot:
        """Return the account with the cash and shares its open orders are holding."""
        context = self.context(profile, run_id, include_closed=True)
        run = context.run
        config = context.config
        account = context.account
        last = context.state
        cash, shares = reservations(account, config, run.config.get('short_selling'))
        return ExecutionSnapshot(
            run_id,
            run.status,
            run.time_ns,
            last['market_sequence'],
            account,
            cash,
            account.cash - cash,
            tuple(sorted(shares.items())),
            config,
        )

    def bind(
        self,
        profile: str,
        run_id: str,
        maker: str,
        market: MarketSnapshot,
        source: Any = None,
    ) -> None:
        """Attach a fresh run to its market and record the starting frontier.

        The run must have no history. A prepared experiment must start exactly at the
        market position saved with it. ``source`` describes the recording so that a
        recorded replay can later check it is reading the same data.
        """
        context = self.context(profile, run_id)
        run = context.run
        last = context.state
        run.require_authority(maker)
        if self.repo.events.has_run(run_id) or last['tick']:
            raise ContractError('run already has an execution owner or history')
        research_source = run.config.get('research_source') or {}
        origin = research_source.get('start_origin')
        if origin is None:
            self.synchronized(run, last, market)
        elif (
            origin != dict(sequence=market.sequence, time_ns=market.time_ns)
            or market.status not in ('ready', 'paused')
            or sorted(run.universe) != sorted(snapshot_universe(market))
        ):
            raise ContractError('prepared market differs from the experiment start point')
        if _journals_state(run):
            config = copy_json(run.config)
            config['replay'] = dict(
                version=1,
                source=source,
                origin=asdict(market),
                initial_maker=maker,
                initial_config=copy_json(run.config),
            )
            self.repo.runs.save_config(run_id, config)
        self.accounts.record_run_event(
            run,
            f'{run_id}:tick:1',
            market.time_ns,
            'execution_tick',
            dict(
                tick=1,
                market_sequence=market.sequence,
                event_kind='bound',
                source_type='',
                market=asdict(market),
                bids=refresh([], market.bids),
                asks=refresh([], market.asks),
                # Several symbols: each symbol's book and supply budget, by symbol.
                **(dict(books=start_books(market)) if len(run.universe) > 1 else {}),
            ),
        )

    @staticmethod
    def synchronized(run: Run, last: Frontier, market: MarketSnapshot) -> None:
        """Require the market to be exactly where this run last processed it."""
        if market.time_ns != run.time_ns or market.sequence != last['market_sequence']:
            raise ContractError('market was advanced outside the execution session')
        # An order passes the book of its own symbol, one symbol of the universe.
        if market.symbol not in run.universe:
            raise ContractError('market symbol is outside the run universe')

    def _operation_time(
        self, run: Run, market: MarketSnapshot, operation_position: Optional[Dict[str, Any]]
    ) -> int:
        """Validate positioned tools inside the financial UOW; unpositioned operations use source time."""
        self._agent_position = None
        if operation_position is None:
            return market.time_ns
        validate_agent_position(operation_position)
        if (
            not run.config.get('agent_experiment_id')
            and run.config.get('research_source', {}).get('execution_mode') != 'live'
        ):
            raise ContractError('positioned tool requires an agent run')
        if (
            int(operation_position['sequence']) != market.sequence
            or int(operation_position['time_ns']) < market.time_ns
        ):
            raise ContractError('operation differs from executed source prefix')
        time_ns = int(operation_position['time_ns'])
        integer(time_ns, 'operation time', market.time_ns)
        self._agent_position = copy_json(operation_position)
        return time_ns

    def _existing_intent_result(
        self,
        run: Run,
        account: AccountSnapshot,
        intent: Intent,
        maker: str,
        market_order: Optional[MarketOrder] = None,
    ) -> Optional[OrderResult]:
        """Return a committed result for an exact repeat, rejecting changed content.

        A repeated market order is compared by what the trader sent, not by its
        protection price, which depends on the book at the time.
        """
        intent_id = f'{run.id}:intent:{intent.client_id}'
        existing = self.repo.events.find(run.account_id, intent_id)
        if existing is None:
            return None
        same = (
            existing.data.get('market_order') == asdict(market_order)
            if market_order is not None
            else existing.data['intent'] == asdict(intent) and 'market_order' not in existing.data
        )
        if not same or existing.data['maker_id'] != maker:
            raise ContractError('client ID reused with different content')
        resolution = self.repo.events.find(
            run.account_id, f'{intent_id}:accepted'
        ) or self.repo.events.find(run.account_id, f'{intent_id}:rejected')
        if resolution is None:
            raise ContractError('intent has no committed resolution')
        order = next((o for o in account.orders if o.intent_id == intent_id), None)
        return OrderResult(
            intent_id,
            order.order_id if order else None,
            order.status if order else 'rejected',
            resolution.data.get('reason'),
        )

    def submit(
        self,
        profile: str,
        run_id: str,
        maker: str,
        intent: Intent,
        market: MarketSnapshot,
        *,
        operation_position: Optional[Dict[str, Any]] = None,
        market_order: Optional[MarketOrder] = None,
    ) -> OrderResult:
        """Record an order intent and either accept or reject it.

        ``market_order`` marks the intent as that market order, priced by submit_market;
        it is recorded with the intent and the acceptance.

        The intent is always recorded. An accepted order becomes eligible to fill after
        the configured latency. Repeating a client ID with identical content returns
        the original result; reusing it with different content is an error.

        Raises:
            ContractError: If the maker has no authority, the market moved outside this
                run, the intent is malformed, or a client ID is reused with new content.
        """
        context = self.context(profile, run_id, include_closed=True)
        run = context.run
        config = context.config
        account = context.account
        last = context.state
        run.require_authority(maker)
        self.synchronized(run, last, market)
        if not isinstance(intent, Intent):
            raise ContractError('expected Intent')
        intent.validate()
        time_ns = self._operation_time(run, market, operation_position)
        intent_id = f'{run_id}:intent:{intent.client_id}'
        existing = self._existing_intent_result(run, account, intent, maker, market_order)
        if existing is not None:
            return existing
        short = run.config.get('short_selling')
        reason = rejection_reason(intent, account, config, replace(market, time_ns=time_ns), short)
        if market_order is not None and protection_price(market_order.side, market) is None:
            reason = 'no opposite quote to price a market order'
        intent_data = dict(
            intent_id=intent_id, run_id=run_id, maker_id=maker, intent=asdict(intent)
        )
        if market_order is not None:
            # Present only for market orders, so earlier records keep their exact form.
            intent_data['market_order'] = asdict(market_order)
        self.record_event(run, intent_id, time_ns, 'intent', intent_data)
        if reason:
            self.record_event(
                run,
                f'{intent_id}:rejected',
                time_ns,
                'rejected',
                dict(intent_id=intent_id, reason=reason),
            )
            return OrderResult(intent_id, None, 'rejected', reason)
        # IDs do not depend on wall-clock time or randomness within this run.
        order_id = f'{run_id}:order:{sha256(intent.client_id.encode()).hexdigest()}'
        delay = order_delay(config, order_id)
        integer(time_ns + delay, 'eligible time')
        accepted = dict(
            intent_id=intent_id,
            order_id=order_id,
            submitted_sequence=market.sequence,
            eligible_ns=time_ns + delay,
            fee_per_share=config.fee_per_share,
        )
        if market_order is not None:
            accepted.update(order_type='market', protection_bps=MARKET_PROTECTION_BPS)
        if short:
            accepted['short_selling'] = True
        self.record_event(run, f'{intent_id}:accepted', time_ns, 'accepted', accepted)
        return OrderResult(intent_id, order_id, 'accepted')

    def submit_market(
        self,
        profile: str,
        run_id: str,
        maker: str,
        order: MarketOrder,
        market: MarketSnapshot,
        *,
        operation_position: Optional[Dict[str, Any]] = None,
    ) -> OrderResult:
        """Accept or reject a market order as a limit order at its protection price.

        The protection price comes from this market snapshot (docs/reference/orders.md).
        Without an opposite quote the order is recorded and rejected.
        """
        if not isinstance(order, MarketOrder):
            raise ContractError('expected MarketOrder')
        order.validate()
        price = protection_price(order.side, market)
        # Without a quote the placeholder limit of one unit is recorded with the
        # rejection; submit() rejects an unpriced market order before acceptance.
        intent = Intent(order.client_id, order.symbol, order.side, order.quantity, price or 1)
        return self.submit(
            profile,
            run_id,
            maker,
            intent,
            market,
            operation_position=operation_position,
            market_order=order,
        )

    def cancel(
        self,
        profile: str,
        run_id: str,
        maker: str,
        order_id: str,
        market: MarketSnapshot,
        *,
        operation_position: Optional[Dict[str, Any]] = None,
    ) -> OrderSnapshot:
        """Cancel an open order of this run and return its updated state.

        Cancelling an already cancelled order returns it unchanged.

        Raises:
            ContractError: If the order is unknown, belongs to another run, or is
                already filled or rejected.
        """
        context = self.context(profile, run_id, include_closed=True)
        run = context.run
        account = context.account
        last = context.state
        run.require_authority(maker)
        self.synchronized(run, last, market)
        time_ns = self._operation_time(run, market, operation_position)
        label(order_id, 'order ID')
        order = next(
            (o for o in account.orders if o.order_id == order_id and o.run_id == run_id), None
        )
        if order is None:
            raise ContractError('unknown order or wrong run')
        if order.status == 'cancelled':
            return order
        if order.status not in OPEN:
            raise ContractError('order is not open')
        self.record_event(
            run,
            f'{run_id}:cancel:{order_id}',
            time_ns,
            'cancelled',
            dict(order_id=order_id, reason='user cancellation'),
        )
        return next(
            o
            for o in self.accounts.snapshot(profile, run.account_id).orders
            if o.order_id == order_id
        )

    def record_event(
        self, run: Run, event_id: str, time_ns: int, kind: str, data: Dict[str, Any]
    ) -> Any:
        """Append one ledger event for this run and mark the cached account as stale."""
        if _journals_state(run):
            data = {**data, 'position': position(self._require_state())}
        if self._agent_position is not None:
            data = {**data, 'agent_position': copy_json(self._agent_position)}
        result = self.accounts.record_run_event(run, event_id, time_ns, kind, data)
        self._account_dirty = True
        return result

    def _require_state(self) -> Frontier:
        """The frontier of a run that journals its state; such a run always has one."""
        if self.runtime_state is None:
            raise ContractError('run has no execution state')
        return self.runtime_state

    def advance(self, profile: str, run_id: str, event: MarketEvent) -> Tuple[Fill, ...]:
        """Process one market event in this transaction and record any fills.

        Used when the run has no resident owner; a resident owner advances in memory
        and calls ``persist_fills`` only when an event produced fills.
        """
        context = self.context(profile, run_id)
        run = context.run
        config = context.config
        account = context.account
        last = context.state
        run.require_active()
        if event.snapshot.symbol not in run.universe:
            raise ContractError('market symbol mismatch')
        accepted = accepted_orders(self.repo, run_id, account)
        state, fills = advance(config, account, accepted, last, event)
        journals_state = _journals_state(run)
        if journals_state:
            self.runtime_state = state
        for index, data in enumerate(fills, 1):
            self.record_event(
                run, f'{run_id}:fill:{state["tick"]}:{index}', event.time_ns, 'fill', data
            )
        if journals_state:
            self.repo.runs.save_execution_state(run_id, state)
        else:
            self.accounts.record_run_event(
                run, f'{run_id}:tick:{state["tick"]}', event.time_ns, 'execution_tick', state
            )
        return fills

    def persist_fills(self, profile: str, run_id: str, fills: Sequence[Fill]) -> Sequence[Fill]:
        """Record fills that the run's owner already computed in memory."""
        context = self.context(profile, run_id)
        run = context.run
        last = context.state
        run.require_active()
        for index, data in enumerate(fills, 1):
            self.record_event(
                run,
                f'{run_id}:fill:{last["tick"]}:{index}',
                last['market']['time_ns'],
                'fill',
                data,
            )
        return fills

    def finish(
        self, profile: str, run_id: str, status: str, time_ns: int, reason: str
    ) -> ExecutionSnapshot:
        """End the run: cancel its open orders, record the end and archive its state.

        Args:
            status: ``completed``, ``stopped`` or ``failed``.
            time_ns: End time; an earlier value is raised to the run's current time.
            reason: Human-readable cause, stored on the end and cancellation events.

        Returns:
            The final account snapshot.
        """
        context = self.context(profile, run_id)
        run = context.run
        account = context.account
        run.require_active()
        if status not in ('completed', 'stopped', 'failed'):
            raise ContractError('invalid execution terminal status')
        integer(time_ns, 'simulation time')
        label(reason, 'execution end reason')
        time_ns = max(time_ns, run.time_ns)
        if self.runtime is None and self.runtime_state is not None:
            self.runtime_state = {
                **self.runtime_state,
                'ordinal': self.runtime_state.get('ordinal', 0) + 1,
            }
        for order in account.orders:
            if order.run_id == run_id and order.status in OPEN:
                self.record_event(
                    run,
                    f'{run_id}:end-cancel:{order.order_id}',
                    time_ns,
                    'cancelled',
                    dict(order_id=order.order_id, reason=reason),
                )
        self.record_event(
            run, f'{run_id}:end', time_ns, 'execution_end', dict(reason=reason, status=status)
        )
        if self.runtime_state is not None:
            if self.runtime is None:
                sequence = self.repo.checkpoints.get(run.account_id).sequence
                self.runtime_state = {
                    **self.runtime_state,
                    'account_sequence': sequence,
                    'decision_index': run.policy_state.get('_record_count', 0),
                }
                self.repo.runs.save_execution_state(run_id, self.runtime_state)
            self.repo.runs.save_execution_archive(run_id, self.runtime_state)
        self.accounts.finish_run(profile, run_id, status)
        self._context_run = self.repo.runs.get(run_id)
        return self.snapshot(profile, run_id)

    def publication(
        self, profile: str, run_id: str, previous: Optional[RuntimeData] = None
    ) -> RuntimeData:
        """Build the post-transaction read model; the Coordinator publishes it after commit."""
        self._context_run = self.repo.runs.get(run_id) if previous is not None else None
        context = self.context(profile, run_id, include_closed=True)
        run = context.run
        account = context.account
        last = context.state
        return build_publication(
            self.accounts,
            profile,
            run,
            account,
            last,
            self.snapshot(profile, run_id),
            previous=previous,
            account_changed=self._account_dirty,
        )

    def complete_change(
        self,
        profile: str,
        run_id: str,
        result: Any,
        *,
        previous: Optional[RuntimeData] = None,
    ) -> ExecutionChange:
        """Finish a change for a run with a resident owner.

        Saves the frontier together with the account and decision positions it
        corresponds to, then builds the read data the owner will hold after commit.
        ``previous`` is the owner's data before this change; without an owner there is
        nothing to save or publish.
        """
        if previous is not None:
            current = self.repo.runs.get(run_id)
            sequence = (
                self.repo.checkpoints.get(current.account_id).sequence
                if self._account_dirty
                else previous.account.sequence
            )
            stored = {
                **self._require_state(),
                'account_sequence': sequence,
                'decision_index': current.policy_state.get('_record_count', 0),
            }
            self.repo.runs.save_execution_state(run_id, stored)
        data = self.publication(profile, run_id, previous) if previous is not None else None
        return ExecutionChange(result, data)

    def direct_operation(
        self,
        method: str,
        profile: str,
        run_id: str,
        maker: str,
        value: Any,
        market: MarketSnapshot,
    ) -> Any:
        """Run ``submit`` or ``cancel`` and journal the attempt with its outcome.

        A rejection is returned, not raised, so the journal entry commits with the
        transaction; ExecutionCoordinator raises it afterwards.
        """
        run = self.accounts.owned_run(profile, run_id)
        run.require_authority(maker)
        journal = copy_json(run.policy_state)
        records = journal.setdefault('execution_operations', [])
        if len(records) >= OPERATION_JOURNAL_LIMIT:
            raise ContractError('execution operation budget exhausted')
        try:
            result = getattr(self, method)(profile, run_id, maker, value, market)
            outcome = dict(status='ok', result=asdict(result))
        except ContractError as exc:
            result = exc
            outcome = dict(status='error', error=str(exc))
        if self.runtime_state is not None:
            records.append(
                dict(
                    kind=method,
                    maker_id=maker,
                    input=asdict(value) if method == 'submit' else value,
                    position=position(self.runtime_state),
                    outcome=outcome,
                )
            )
            self.repo.runs.save_policy_state(run_id, journal)
        return result
