"""Commit decision audit, policy state, and resulting OMS actions atomically."""

from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple, Union

from sim_platform.domain.models import ContractError, Intent, MarketOrder
from sim_platform.domain.json_values import copy_json
from sim_platform.market.models import book_for

if TYPE_CHECKING:  # Annotation only: these modules are imported lazily at runtime.
    from sim_platform.decisions.policy.models import Action, DecisionJournal, DecisionRecord
    from sim_platform.decisions.policy.models import PolicyDescriptor
    from sim_platform.domain.models import OrderSnapshot
    from sim_platform.execution.models import ExecutionSnapshot, OrderResult
    from sim_platform.execution.service import ExecutionService
    from sim_platform.market.models import MarketSnapshot

    # The result of one submit (OrderResult) or cancel (OrderSnapshot) action.
    ActionResult = Union[OrderResult, OrderSnapshot]


def execute_action(
    service: ExecutionService,
    profile: str,
    run_id: str,
    maker: str,
    market: MarketSnapshot,
    action: Action,
    *,
    position: Optional[Dict[str, Any]] = None,
) -> ActionResult:
    """Shared OMS action dispatch for policy decisions and positioned agent tools."""
    if action['kind'] == 'submit':
        intent = Intent(**action['intent'])
        return service.submit(
            profile,
            run_id,
            maker,
            intent,
            book_for(market, intent.symbol),
            operation_position=position,
        )
    if action['kind'] == 'submit_market':
        market_order = MarketOrder(**action['order'])
        return service.submit_market(
            profile,
            run_id,
            maker,
            market_order,
            book_for(market, market_order.symbol),
            operation_position=position,
        )
    if action['kind'] != 'cancel':
        raise ContractError('unsupported decision action')
    if 'order_id' in action:
        order_id = action['order_id']
    else:
        account = service.snapshot(profile, run_id).account
        intent_id = f"{run_id}:intent:{action['client_id']}"
        order = next(
            (o for o in account.orders if o.run_id == run_id and o.intent_id == intent_id), None
        )
        if order is None:
            raise ContractError('unknown cancellation client ID')
        order_id = order.order_id
    return service.cancel(profile, run_id, maker, order_id, market, operation_position=position)


def commit(
    service: ExecutionService,
    profile: str,
    run_id: str,
    maker: str,
    market: MarketSnapshot,
    record: Optional[DecisionRecord] = None,
    expected_state: Optional[DecisionJournal] = None,
    *,
    action: Optional[Action] = None,
    position: Optional[Dict[str, Any]] = None,
) -> Union[DecisionRecord, Tuple[ActionResult, ExecutionSnapshot]]:
    """One submission path; policy audit and tool JSON keep their own contracts.

    Policy calls supply record/journal; tools supply one positioned action.
    Neither form can silently omit its required context or mix the two forms.
    Both execute through execute_action in the caller's financial UOW.
    A tool call returns (action result, account snapshot); a policy call returns
    the committed audit record.
    """
    if action is not None:
        if record is not None or expected_state is not None or position is None:
            raise ContractError('tool submission requires only action and position')
        result = execute_action(service, profile, run_id, maker, market, action, position=position)
        return result, service.snapshot(profile, run_id)
    if record is None or expected_state is None:
        raise ContractError('policy submission requires record and journal')
    context = service.context(profile, run_id)
    run = context.run
    last = context.state
    from sim_platform.decisions.policy.handoffs import StaleAuthority, decision_state

    run.require_active()
    if maker != run.maker_id or record.get('generation', 0) != run.policy_state.get(
        'generation', 0
    ):
        raise StaleAuthority('stale authority generation')
    service.synchronized(run, last, market)
    if decision_state(run.policy_state) != decision_state(expected_state):
        raise ContractError('decision state changed during invocation')
    state = copy_json(run.policy_state)
    if record['index'] != state['_record_count'] + 1:
        raise ContractError('decision audit index mismatch')
    results: List[Dict[str, Any]] = []
    if record['status'] == 'ok':
        for action in record['response']['actions']:
            try:
                results.append(
                    asdict(
                        execute_action(
                            service,
                            profile,
                            run_id,
                            maker,
                            market,
                            action,
                            **({'position': position} if position is not None else {}),
                        )
                    )
                )
            except ContractError as exc:
                results.append({'status': 'rejected', 'reason': str(exc)})
        state['state'] = record['response']['state']
    committed: DecisionRecord = copy_json(record)
    if position is not None:
        committed['operation_position'] = copy_json(position)
    committed['results'] = results
    if service.runtime_state is not None:
        # Aliased so the import does not rebind this function's ``position`` parameter.
        from sim_platform.execution.position import position as frontier_position

        committed['position'] = frontier_position(service.runtime_state)
    service.repo.runs.append_record(run_id, committed)
    state['_record_count'] += 1
    service.repo.runs.save_policy_state(run_id, state)
    return committed


def managed_handoff(
    service: ExecutionService,
    method: str,
    profile: str,
    run_id: str,
    maker: str,
    generation: int,
    market: MarketSnapshot,
    boundary: int,
    target: Optional[str],
    descriptor: PolicyDescriptor,
    config: Dict[str, Any],
    state_mode: str,
    argument: Any,
) -> Tuple[str, Dict[str, Any]]:
    """Create the target maker if needed, then run handoffs.<method> in the same UOW.

    ``method`` is 'switch' (``argument`` is the reason) or 'schedule' (``argument`` is
    the condition). Returns the target maker and the handoff or switch record.
    """
    from sim_platform.decisions.policy import handoffs

    service.accounts.owned_run(profile, run_id).require_authority(maker)
    if target is None:
        target = service.accounts.makers.create_maker(
            profile, descriptor['name'], descriptor['kind'], descriptor['version']
        )
    record = getattr(handoffs, method)(
        service,
        profile,
        run_id,
        maker,
        generation,
        market,
        boundary,
        target,
        descriptor,
        config,
        state_mode,
        argument,
    )
    return target, record


def initialize_decisions(service: ExecutionService, profile: str, run_id: str, maker: str) -> None:
    """Create an empty decision journal; refuses a run that already has one."""
    run = service.accounts.owned_run(profile, run_id)
    run.require_authority(maker)
    if run.policy_state:
        raise ContractError('decision journal already exists')
    service.repo.runs.save_policy_state(run_id, {'state': {}, '_record_count': 0})
