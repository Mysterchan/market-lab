"""Compare financial outcomes without depending on destination-generated IDs."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List

from sim_platform.domain.json_values import copy_json


def financial_values(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    """Return balances and order terms, independent of account/run identities."""
    account = snapshot.get('account', snapshot)
    orders = sorted(
        (o['symbol'], o['side'], o['quantity'], o['limit_price'], o['filled'], o['status'])
        for o in account['orders']
    )
    return copy_json(
        dict(
            cash=account['cash'],
            fees=account['fees'],
            realized_pnl=account['realized_pnl'],
            positions=account['positions'],
            orders=orders,
            reserved_cash=snapshot.get('reserved_cash', 0),
            reserved_shares=snapshot.get('reserved_shares', []),
        )
    )


def financial_events(events: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Keep original order/ledger effects in order; remove storage-only identities.

    The list is comparison evidence and a display timeline, never execution input.
    Pause, model attempts and other non-financial audit events are excluded.
    """
    intents: Dict[str, Dict[str, Any]] = {}
    orders: Dict[str, str] = {}
    result: List[Dict[str, Any]] = []
    for event in events:
        kind, data = event['kind'], event['data']
        if kind == 'intent':
            intents[data['intent_id']] = dict(data['intent'])
            continue
        if kind not in ('accepted', 'rejected', 'fill', 'cancelled'):
            continue
        if kind in ('accepted', 'rejected'):
            intent = intents[data['intent_id']]
            detail: Dict[str, Any] = dict(intent=intent)
            if kind == 'accepted':
                orders[data['order_id']] = intent['client_id']
                detail.update(
                    eligible_ns=data['eligible_ns'],
                    submitted_sequence=data['submitted_sequence'],
                    fee_per_share=data['fee_per_share'],
                )
            else:
                detail['reason'] = data['reason']
        else:
            detail = dict(client_id=orders[data['order_id']])
            if kind == 'fill':
                detail.update(
                    {
                        key: data[key]
                        for key in ('quantity', 'price', 'fee', 'model', 'market_sequence')
                    }
                )
        result.append(dict(time_ns=event['time_ns'], kind=kind, data=detail))
    return copy_json(result)
