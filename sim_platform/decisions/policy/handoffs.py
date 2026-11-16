"""Transactional authority changes; no account or order mutations."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any, Dict, Optional

from sim_platform.domain.models import ContractError, integer, label
from sim_platform.domain.json_values import copy_json
from sim_platform.decisions.policy.models import (
    CAPABILITIES,
    DecisionConfig,
    DecisionJournal,
    Observation,
    PolicyDescriptor,
)

if TYPE_CHECKING:  # Annotation only: the transaction-bound service and market values.
    from sim_platform.execution.models import ExecutionSnapshot
    from sim_platform.execution.service import ExecutionService
    from sim_platform.market.models import MarketSnapshot

# Each journal list (handoffs, scheduled switches, authority rejections) stops at this size.
JOURNAL_ENTRY_LIMIT = 1000
# A scheduled switch time must be a valid market time: below 100 years of nanoseconds
# from the recording's first midnight (same value as market.models.MAX_TIME_NS).
MAX_TIME_NS = 36_525 * 86_400_000_000_000

# One completed handoff in the journal's 'handoffs' list: 'index', 'boundary',
# 'time_ns', 'market_sequence', 'from_maker', 'to_maker', 'from_generation',
# 'generation', 'state_mode', 'reason', 'from_policy', 'to_policy', 'incoming_state',
# 'account', 'schedule_id' and, when known, 'position'.
HandoffRecord = Dict[str, Any]
# One scheduled switch in the journal's 'switches' list: 'id', 'boundary', 'time_ns',
# 'generation', 'target', 'policy', 'config', 'state_mode', 'condition', 'status'
# ('pending', 'fired' or 'invalidated') and, when known, 'position'.
SwitchRecord = Dict[str, Any]
# When a scheduled switch fires: {'kind': 'time', 'time_ns': int} or
# {'kind': 'position_at_least', 'symbol': str, 'quantity': int}.
HandoffCondition = Dict[str, Any]


class StaleAuthority(ContractError):
    """A maker or generation acted after authority passed to another maker."""


@dataclass(frozen=True)
class DecisionRequest:
    """A captured manual decision point; the response is checked against it later."""

    maker_id: str
    generation: int
    observation: Observation
    journal: DecisionJournal


def decision_state(journal: DecisionJournal) -> DecisionJournal:
    """Rejection diagnostics do not invalidate an otherwise unchanged observation."""
    return {
        k: v for k, v in journal.items() if k not in ('authority_rejections', 'research_archive')
    }


def validate_target(
    service: ExecutionService,
    profile: str,
    maker: str,
    descriptor: PolicyDescriptor,
    config: Dict[str, Any],
) -> None:
    """Check that the target maker is registered with the same kind and a valid config."""
    registered = service.repo.makers.owned(maker, profile)
    DecisionConfig(**config)
    if registered.kind != descriptor['kind']:
        raise ContractError('policy kind differs from registered maker')
    if any(c not in CAPABILITIES for c in descriptor['requires']):
        raise ContractError('required data capability is unavailable')


def switch(
    service: ExecutionService,
    profile: str,
    run_id: str,
    maker: str,
    generation: int,
    market: MarketSnapshot,
    boundary: int,
    target: str,
    descriptor: PolicyDescriptor,
    config: Dict[str, Any],
    state_mode: str,
    reason: str,
    schedule_id: Optional[int] = None,
) -> HandoffRecord:
    """Pass decision authority to ``target`` now and return a copy of the handoff record."""
    context = service.context(profile, run_id, include_closed=True)
    run = context.run
    account = context.account
    last = context.state
    run.require_authority(maker)
    service.synchronized(run, last, market)
    journal = copy_json(run.policy_state)
    if generation != journal.get('generation', 0):
        raise StaleAuthority('stale authority generation')
    validate_target(service, profile, target, descriptor, config)
    if target == maker:
        raise ContractError('handoff requires a different maker')
    if state_mode not in ('reset', 'restore'):
        raise ContractError('state mode must be reset or restore')
    label(reason, 'handoff reason')
    handoffs = journal.setdefault('handoffs', [])
    if len(handoffs) >= JOURNAL_ENTRY_LIMIT:
        raise ContractError('handoff budget exhausted')
    states = journal.setdefault('saved_states', {})
    previous = run.config['decision']
    states[maker] = {'policy': previous['policy'], 'state': copy_json(journal['state'])}
    if state_mode == 'restore':
        saved = states.get(target)
        if saved is None or saved['policy'] != descriptor:
            raise ContractError('no compatible saved policy state')
        next_state = saved['state']
    else:
        next_state = {}
    record = dict(
        index=len(handoffs) + 1,
        boundary=boundary,
        time_ns=market.time_ns,
        market_sequence=market.sequence,
        from_maker=maker,
        to_maker=target,
        from_generation=generation,
        generation=generation + 1,
        state_mode=state_mode,
        reason=reason,
        from_policy=previous,
        to_policy={'policy': descriptor, 'config': config},
        incoming_state=copy_json(next_state),
        account=asdict(account),
        schedule_id=schedule_id,
    )
    for scheduled in journal.get('switches', []):
        if scheduled['status'] == 'pending':
            scheduled['status'] = 'fired' if scheduled['id'] == schedule_id else 'invalidated'
    if service.runtime_state is not None:
        from sim_platform.execution.position import position

        record['position'] = position(service.runtime_state)
    handoffs.append(record)
    journal['generation'], journal['state'] = generation + 1, copy_json(next_state)
    updated = copy_json(run.config)
    updated['decision'] = {'policy': descriptor, 'config': config}
    service.repo.runs.set_decision_owner(run_id, target, updated, journal)
    return copy_json(record)


def schedule(
    service: ExecutionService,
    profile: str,
    run_id: str,
    maker: str,
    generation: int,
    market: MarketSnapshot,
    boundary: int,
    target: str,
    descriptor: PolicyDescriptor,
    config: Dict[str, Any],
    state_mode: str,
    condition: HandoffCondition,
) -> SwitchRecord:
    """Record a pending switch to ``target`` that fires when ``condition`` is met."""
    context = service.context(profile, run_id)
    run = context.run
    last = context.state
    run.require_authority(maker)
    service.synchronized(run, last, market)
    validate_target(service, profile, target, descriptor, config)
    if target == maker or state_mode not in ('reset', 'restore'):
        raise ContractError('invalid scheduled handoff target or state mode')
    journal = copy_json(run.policy_state)
    if generation != journal.get('generation', 0):
        raise StaleAuthority('stale authority generation')
    if state_mode == 'restore':
        saved = journal.get('saved_states', {}).get(target)
        if saved is None or saved['policy'] != descriptor:
            raise ContractError('no compatible saved policy state')
    condition = copy_json(condition)
    if not isinstance(condition, dict):
        raise ContractError('handoff condition must be an object')
    if condition.get('kind') == 'time' and set(condition) == {'kind', 'time_ns'}:
        integer(condition['time_ns'], 'switch time')
        if condition['time_ns'] < market.time_ns or condition['time_ns'] >= MAX_TIME_NS:
            raise ContractError('switch time must be the current or a future market time')
    elif condition.get('kind') == 'position_at_least' and set(condition) == {
        'kind',
        'symbol',
        'quantity',
    }:
        integer(condition['quantity'], 'position threshold', 1)
        if condition['symbol'] != market.symbol:
            raise ContractError('condition symbol outside run universe')
    else:
        raise ContractError('unsupported handoff condition')
    switches = journal.setdefault('switches', [])
    if len(switches) >= JOURNAL_ENTRY_LIMIT:
        raise ContractError('scheduled handoff budget exhausted')
    record = dict(
        id=len(switches) + 1,
        boundary=boundary,
        time_ns=market.time_ns,
        generation=generation,
        target=target,
        policy=descriptor,
        config=config,
        state_mode=state_mode,
        condition=condition,
        status='pending',
    )
    if service.runtime_state is not None:
        from sim_platform.execution.position import position

        record['position'] = position(service.runtime_state)
    switches.append(record)
    service.repo.runs.save_policy_state(run_id, journal)
    return record


def reject(
    service: ExecutionService,
    profile: str,
    run_id: str,
    maker: str,
    generation: int,
    reason: str,
) -> None:
    """Audit an instruction refused because its maker or generation no longer holds authority."""
    context = service.context(profile, run_id)
    run = context.run
    run.require_active()
    journal = copy_json(run.policy_state)
    rejections = journal.setdefault('authority_rejections', [])
    if len(rejections) >= JOURNAL_ENTRY_LIMIT:
        raise ContractError('authority rejection budget exhausted')
    record = dict(
        maker_id=maker,
        generation=generation,
        current_maker=run.maker_id,
        current_generation=journal.get('generation', 0),
        time_ns=run.time_ns,
        reason=reason,
    )
    if service.runtime_state is not None:
        from sim_platform.execution.position import position

        record['position'] = position(service.runtime_state)
    rejections.append(record)
    service.repo.runs.save_policy_state(run_id, journal)


def condition_met(condition: HandoffCondition, snapshot: ExecutionSnapshot) -> bool:
    """Whether a scheduled switch's time or position threshold has been reached."""
    if condition['kind'] == 'time':
        return snapshot.time_ns >= condition['time_ns']
    return (
        sum(p.quantity for p in snapshot.account.positions if p.symbol == condition['symbol'])
        >= condition['quantity']
    )
