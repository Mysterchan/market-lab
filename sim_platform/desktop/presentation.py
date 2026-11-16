"""Display formatting independent of widget ownership."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, Optional, Tuple

# Performance figures live in research.metrics; re-exported for Desktop callers.
from sim_platform.research.metrics import (  # noqa: F401
    RELIABLE_SHARPE_RETURNS,
    TRADING_DAYS_PER_YEAR,
    daily_sharpe,
    fill_rows,
    performance,
    portfolio_daily_sharpe,
    portfolio_series,
    round_trips,
    trade_statistics,
)

# Money units per USD, and nanoseconds per second.
PRICE_SCALE = 10000
NS_PER_SECOND = 1_000_000_000

# A detached session view (see research.views).
View = Dict[str, Any]


def file_size(value: int) -> Optional[str]:
    """Format file sizes in decimal units, matching macOS storage displays."""
    if value < 1000:
        return f'{value:,} bytes'
    for unit, scale in (('TB', 10**12), ('GB', 10**9), ('MB', 10**6), ('KB', 10**3)):
        if value >= scale:
            return f'{value / scale:,.2f} {unit}'
    return None


def usd(value: Optional[int]) -> str:
    return '—' if value is None else f'${Decimal(value) / PRICE_SCALE:,.4f}'


def money(text: str) -> int:
    from sim_platform.domain.inputs import money as parse

    return parse(text)


def clock(value: Optional[int]) -> str:
    """Market time as HH:MM:SS.nnnnnnnnn, prefixed with the day number after day 0.

    Market time counts from the first day's midnight, so a multi-day recording shows
    for example ``D+3 09:30:00.000000000`` on its fourth day.
    """
    if value is None:
        return '—'
    day, within = divmod(value, 86_400 * NS_PER_SECOND)
    seconds, ns = divmod(within, NS_PER_SECOND)
    text = f'{seconds // 3600:02}:{seconds // 60 % 60:02}:{seconds % 60:02}.{ns:09}'
    return f'D+{day} {text}' if day else text


def state_label(value: str) -> str:
    return {
        'ready': 'Ready',
        'paused': 'Paused',
        'playing': 'Playing',
        'completed': 'Completed',
        'failed': 'Failed',
        'closed': 'Closed',
        'running': 'Running',
        'stopped': 'Stopped',
        'accepted': 'Accepted',
        'rejected': 'Rejected',
        'partial': 'Partially filled',
        'filled': 'Filled',
        'cancelled': 'Cancelled',
        'ok': 'OK',
        'error': 'Error',
        'timeout': 'Timed out',
    }.get(value, value)


def event_description(event: Dict[str, Any]) -> str:
    data, kind = event['data'], event['kind']
    if kind == 'intent':
        order = data['intent']
        return f"{order['client_id']} · {'Buy' if order['side'] == 'buy' else 'Sell'} {order['quantity']} shares · Limit {usd(order['limit_price'])}"
    if kind == 'fill':
        return (
            f"Fill {data['quantity']} shares · Price {usd(data['price'])} · Fees {usd(data['fee'])}"
        )
    if kind == 'execution_tick':
        return 'Market data updated'
    return (
        data.get('reason')
        or data.get('intent_id', '').split(':intent:', 1)[-1]
        or state_label(kind)
    )


# Plain names for account event kinds, as the Activity tab and Records page show them.
EVENT_LABELS = {
    'intent': 'Order sent',
    'accepted': 'Order accepted',
    'rejected': 'Order rejected',
    'fill': 'Fill',
    'cancelled': 'Order cancelled',
    'execution_tick': 'Market checkpoint',
    'execution_end': 'Experiment ended',
    'deposit': 'Cash deposited',
}


def event_text(event: Dict[str, Any], sides: Dict[str, str]) -> Dict[str, str]:
    """A plain label and one line of details for an account event.

    ``sides`` maps order IDs to buy/sell, which a fill record does not repeat.
    """
    data, kind = event.get('data') or {}, event['kind']
    client = str(data.get('intent_id', '')).split(':intent:', 1)[-1]
    details = ''
    if kind == 'intent' and 'intent' in data:
        order = data['intent']
        side = 'Buy' if order['side'] == 'buy' else 'Sell'
        kind_text = (
            'market order' if 'market_order' in data else 'limit ' + amount(order['limit_price'])
        )
        details = f"{order['client_id']} · {side} {order['quantity']} shares · {kind_text}"
    elif kind == 'accepted':
        details = client
        if data.get('order_type') == 'market':
            details += ' · market order'
    elif kind == 'fill':
        side = {'buy': 'Bought', 'sell': 'Sold'}.get(
            sides.get(data.get('order_id', ''), ''), 'Filled'
        )
        details = (
            f"{side} {data['quantity']} shares at {amount(data['price'])}"
            f" · fee {amount(data['fee'])}"
        )
    elif kind in ('rejected', 'cancelled'):
        details = ' · '.join(x for x in (client, data.get('reason')) if x)
    elif kind == 'execution_end':
        details = str(data.get('reason') or data.get('status') or '')
    return dict(label=EVENT_LABELS.get(kind, state_label(kind)), details=details)


def describe_events(view: View) -> View:
    """A copy of the view whose events carry ``label`` and ``details`` for display."""
    events = view.get('events')
    if not events:
        return view
    account = view.get('account') or {}
    sides = {o['order_id']: o['side'] for o in account.get('orders', [])}
    return dict(view, events=[dict(e, **event_text(e, sides)) for e in events])


def amount(value: Optional[int], *, currency: bool = True) -> str:
    """Use cents where exact, retaining four decimals for sub-cent prices/fees."""
    if value is None:
        return '—'
    digits = 2 if value % 100 == 0 else 4
    prefix = '$' if currency else ''
    return f'{prefix}{Decimal(value) / PRICE_SCALE:,.{digits}f}'


def agent_definition(view: View) -> Dict[str, Any]:
    """Read the immutable saved definition attached to this run, not today's revision."""
    return ((view.get('run') or {}).get('config', {}).get('agent') or {}).get('definition', {})


def portfolio_values(view: View) -> Tuple[Optional[int], Optional[int], Optional[int]]:
    """(estimated value, P&L vs the initial cash, cash), or None where unknown.

    Long positions are valued at the best bid and short positions at the best ask.
    """
    account = view.get('account')
    if account is None:
        return None, None, None
    market = view.get('market') or {}
    positions = account['positions']
    # Several symbols: each position is marked against its own symbol's book.
    books = {s: m['market'] for s, m in (view.get('markets') or {}).items()} or {
        market.get('symbol'): market
    }
    value: Optional[int] = account['cash']
    for position in positions:
        book = books.get(position['symbol']) or {}
        # Longs at the best bid; shorts at the best ask, what covering would cost.
        side = (book.get('bids') if position['quantity'] > 0 else book.get('asks')) or []
        if value is None or not side:
            value = None
            break
        value += position['quantity'] * side[0][0]
    initial = (view.get('run') or {}).get('initial_snapshot') or {}
    pnl = (
        value - initial['cash']
        if value is not None and initial and not initial['positions']
        else None
    )
    return value, pnl, account['cash']


def work_status(view: View) -> Tuple[str, Any, Any]:
    """(status title, next due time, error) for the Agent work panel."""
    pipeline = view.get('pipeline') or {}
    controls = view.get('work') or {}
    work, attempt = controls.get('work') or {}, controls.get('attempt') or {}
    state = pipeline.get('state', 'ready')
    if state in ('paused', 'stopped', 'completed', 'failed', 'ended'):
        title = {'ended': 'Experiment ended'}.get(state, state.capitalize())
    elif work.get('failure'):
        title = 'Agent failed · accepted orders remain active'
    elif attempt.get('state') == 'running':
        title = 'Waiting for model response'
    elif attempt.get('state') == 'backoff':
        title = 'Waiting before model retry'
    elif work.get('state') == 'running':
        title = 'Working on the current task'
    else:
        title = 'Waiting for next task' if state == 'running' else 'Ready · press Play to start'
    return title, work.get('due_ns', work.get('due')), pipeline.get('error') or work.get('failure')


# (path, bytes, mtime_ns) -> (first date, last date) or None, so a file is read once.
_SPANS: Dict[Tuple[str, int, int], Optional[Tuple[str, str]]] = {}


def describe_datasets(catalog: Dict[str, Any]) -> Dict[str, Any]:
    """Add what each library file holds: 'kind', and for price bars the 'dates' covered.

    An ITCH file's symbols and dates are only known once it is prepared, so only its
    kind is given. Unreadable or changed files get no dates.
    """
    from pathlib import Path

    from sim_platform.domain.models import ContractError
    from sim_platform.market.prepared.bars import bar_file_span, is_bar_source

    files = []
    for entry in catalog.get('files') or []:
        path = Path(entry['path'])
        bars = is_bar_source(path)
        described = dict(entry, kind='Price bars · CSV' if bars else 'ITCH 5.0 messages')
        if bars and entry.get('status') == 'Available':
            key = (entry['path'], entry.get('bytes', 0), entry.get('mtime_ns', 0))
            if key not in _SPANS:
                try:
                    _SPANS[key] = bar_file_span(path)
                except (ContractError, OSError, UnicodeDecodeError, ValueError):
                    _SPANS[key] = None
            span = _SPANS[key]
            if span is not None:
                described['dates'] = span[0] if span[0] == span[1] else ' to '.join(span)
        files.append(described)
    return dict(catalog, files=files)
