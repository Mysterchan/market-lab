"""Translate recorded Human, Rule and Agent commands; never call their programs."""

from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING, Any, Dict, Optional
from sim_platform.domain.models import ContractError, Intent, MarketOrder
from sim_platform.domain.json_values import copy_json
from sim_platform.agents.tools.protocol.contracts import price_units

if TYPE_CHECKING:  # Annotation only.
    from sim_platform.decisions.policy.models import Action, PolicyResponse
    from sim_platform.decisions.policy.runner import PolicyDecisionRunner

# A recorded command payload: a policy request ('operation' 'manual' or 'commit' with
# 'response') or an Agent tool request ('tool', 'request_id' and 'arguments').
CommandPayload = Dict[str, Any]


def _policy_response(
    operations: Any, profile: str, run_id: str, payload: CommandPayload
) -> Optional[PolicyResponse]:
    """Keep supplied actions and state; failed policy attempts have no actions."""
    response = copy_json(payload['response'])
    if payload['operation'] == 'commit':
        return response['response'] if response['status'] == 'ok' else None
    if response['state'] is None:
        response['state'] = operations.decision_journal(profile, run_id)['state']
    return response


def _tool_action(
    payload: CommandPayload, old_orders: Dict[str, str], new_orders: Dict[str, str]
) -> Action:
    """Build the same financial command used by the original tool handler."""
    args = payload['arguments']
    if payload['tool'] == 'orders.submit_limit':
        intent = Intent(
            payload['request_id'],
            args['symbol'],
            args['side'],
            args['quantity_shares'],
            price_units(args['limit_price_usd']),
        )
        return dict(kind='submit', intent=asdict(intent))
    if payload['tool'] == 'orders.submit_market':
        order = MarketOrder(
            payload['request_id'], args['symbol'], args['side'], args['quantity_shares']
        )
        return dict(kind='submit_market', order=asdict(order))
    if payload['tool'] == 'orders.cancel':
        client = old_orders.get(args['order_id'])
        if client not in new_orders:
            raise ContractError('missing original order mapping in Replay')
        return dict(kind='cancel', order_id=new_orders[client])
    raise ContractError('unsupported recorded financial command')


def apply_recorded_command(
    operations: Any,
    runner: PolicyDecisionRunner,
    profile: str,
    run_id: str,
    position: Dict[str, Any],
    payload: CommandPayload,
    old_orders: Dict[str, str],
    new_orders: Dict[str, str],
) -> None:
    """Execute supplied actions through the shared decision/financial boundary.

    ``operations`` is the App's DatabaseOperations (application package, not typed
    yet). ``old_orders`` maps original order IDs to client IDs; ``new_orders`` maps
    client IDs to destination order IDs and is updated after every command.
    """
    if payload.get('operation') in ('manual', 'commit'):
        response = _policy_response(operations, profile, run_id, payload)
        if response is not None:
            runner.apply_recorded_operation(position, response=response)
    else:
        runner.apply_recorded_operation(
            position, action=_tool_action(payload, old_orders, new_orders)
        )
    for order in runner.snapshot.account.orders:
        client = order.intent_id.split(':intent:', 1)[-1]
        new_orders[client] = order.order_id
