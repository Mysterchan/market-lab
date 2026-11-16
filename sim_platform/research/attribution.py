"""Decision attribution: which reads, model replies and notes led to each order.

An Agent run's log records every tool call in order, grouped into work rounds (one
round per scheduled turn). ``decision_timeline`` lays the rounds out step by step and
links each order to the steps of its round that came before it: the data it read, the
model replies and the journal notes, plus the notes written later in the same round
(often the stated reason). Fills come from the run's recorded events.

Attribution is by order of events within a round, not by causation: a read listed
before an order is evidence the Agent had that information, not proof it used it.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

# Tools that change orders; every other tool only reads or records.
ORDER_TOOLS = (
    'orders.submit_limit',
    'orders.submit_market',
    'orders.cancel',
)
# Tools that end or pause a round of work.
CONTROL_TOOLS = ('control.finish', 'control.wait')
# Longest text kept per step summary.
SUMMARY_CHARACTERS = 240


def _kind(tool: str) -> str:
    if tool in ORDER_TOOLS:
        return 'order'
    if tool == 'journal.write':
        return 'journal'
    if tool in CONTROL_TOOLS:
        return 'control'
    return 'read'


def _summary(tool: str, arguments: Dict[str, Any], result: Dict[str, Any]) -> str:
    data = result.get('data') or {}
    if result.get('status') != 'ok':
        error = result.get('error') or {}
        return f'error {error.get("code", "")}: {error.get("message", "")}'[:SUMMARY_CHARACTERS]
    if tool == 'journal.write':
        return str(arguments.get('text', ''))[:SUMMARY_CHARACTERS]
    if tool == 'market.quote':
        bid, ask = data.get('bid') or {}, data.get('ask') or {}
        return f'{data.get("symbol")} bid {bid.get("price_usd")} ask {ask.get("price_usd")}'
    if tool in ORDER_TOOLS:
        order = data.get('order') or {}
        return (
            f'{order.get("side", "")} {order.get("quantity_shares", "")} {order.get("symbol", "")}'
            f' {data.get("outcome", "")}'
        ).strip()
    text = ', '.join(f'{k}={v}' for k, v in sorted(arguments.items()))
    return f'{tool}({text})'[:SUMMARY_CHARACTERS]


def _step(record: Dict[str, Any]) -> Dict[str, Any]:
    request = record.get('request') or {}
    result = record.get('result') or {}
    tool = request.get('tool', '')
    return dict(
        ordinal=int(record['position']['ordinal']),
        time_ns=int(record['position']['time_ns']),
        kind=_kind(tool),
        tool=tool,
        arguments=request.get('arguments') or {},
        status=result.get('status'),
        summary=_summary(tool, request.get('arguments') or {}, result),
        order_id=((result.get('data') or {}).get('order') or {}).get('order_id'),
        work_id=request.get('work_id'),
    )


def _model_step(record: Dict[str, Any], work_id: Optional[str]) -> Dict[str, Any]:
    usage = ((record.get('details') or {}).get('result') or {}).get('usage') or {}
    tokens = f'{usage.get("input_tokens", "?")} in, {usage.get("output_tokens", "?")} out'
    role = f'{usage["label"]}: ' if isinstance(usage.get('label'), str) else ''
    return dict(
        ordinal=int(record['position']['ordinal']),
        time_ns=int(record['position']['time_ns']),
        kind='model',
        tool='model.request',
        arguments={},
        status='ok',
        summary=f'{role}model reply ({tokens})',
        order_id=None,
        work_id=work_id,
    )


def _fills(events: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    fills: Dict[str, List[Dict[str, Any]]] = {}
    for event in events:
        if event.get('kind') == 'fill':
            data = event.get('data') or {}
            fills.setdefault(str(data.get('order_id')), []).append(
                dict(
                    time_ns=event.get('time_ns'),
                    quantity=data.get('quantity'),
                    price=data.get('price'),
                    fee=data.get('fee'),
                )
            )
    return fills


def decision_timeline(
    archive: Optional[Dict[str, Any]], events: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """Rounds of Agent work and, for each order, the steps that preceded it.

    Args:
        archive: The run's archived Agent log (``Platform.agent_log_archive``).
        events: The run's recorded events (``Platform.run_events``).

    Returns:
        ``{'rounds': [...], 'orders': [...]}``. Each round has ``work_id``,
        ``started_ns``, ``reason`` and ``steps`` (``ordinal``, ``time_ns``, ``kind`` of
        'read', 'model', 'order', 'journal' or 'control', ``tool``, ``arguments``,
        ``status``, ``summary``). Each order has ``order_id``, ``tool``, ``time_ns``,
        ``summary``, ``status``, ``work_id``, ``reads`` and ``model_replies`` (steps
        before it in its round), ``notes_before`` and ``notes_after`` (journal text in
        its round) and ``fills`` (``time_ns``, ``quantity``, integer ``price`` in
        ten-thousandths of a dollar, ``fee``).
    """
    rounds: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None
    for record in (archive or {}).get('records') or []:
        if record.get('kind') == 'control' and record.get('event') == 'work_started':
            details = record.get('details') or {}
            current = dict(
                work_id=details.get('work_id'),
                started_ns=int(details.get('dispatched_ns') or record['position']['time_ns']),
                reason=details.get('reason'),
                steps=[],
            )
            rounds.append(current)
        elif current is None:
            continue
        elif record.get('kind') == 'tool':
            current['steps'].append(_step(record))
        elif record.get('kind') == 'control' and record.get('event') == 'attempt_completed':
            current['steps'].append(_model_step(record, current['work_id']))
    fills = _fills(events)
    orders: List[Dict[str, Any]] = []
    for round_ in rounds:
        steps = round_['steps']
        for index, step in enumerate(steps):
            if step['kind'] != 'order':
                continue
            before, after = steps[:index], steps[index + 1 :]
            orders.append(
                dict(
                    order_id=step['order_id'],
                    tool=step['tool'],
                    time_ns=step['time_ns'],
                    summary=step['summary'],
                    status=step['status'],
                    work_id=round_['work_id'],
                    reads=[s for s in before if s['kind'] == 'read'],
                    model_replies=sum(1 for s in before if s['kind'] == 'model'),
                    notes_before=[s['summary'] for s in before if s['kind'] == 'journal'],
                    notes_after=[s['summary'] for s in after if s['kind'] == 'journal'],
                    fills=fills.get(str(step['order_id']), []) if step['order_id'] else [],
                )
            )
    return dict(rounds=rounds, orders=orders)
