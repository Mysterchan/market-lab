"""Per-run performance figures computed from a saved run's report view.

Pure functions with no Desktop or persistence access. The Desktop result page, the
batch API and evaluation reports all use these definitions (docs/user-guide/results.md).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

# A detached session view (see research.views).
View = Dict[str, Any]


def fill_rows(view: View) -> List[Dict[str, Any]]:
    """Published fills joined with their order's side, for tables and chart marks."""
    account = view.get('account') or {}
    sides = {o['order_id']: o['side'] for o in account.get('orders', [])}
    symbols = {o['order_id']: o.get('symbol') for o in account.get('orders', [])}
    fills = view.get('fills')
    if fills is None:
        fills = [e for e in view.get('events') or [] if e['kind'] == 'fill']
    return [
        dict(
            time_ns=e['time_ns'],
            side=sides.get(e['data']['order_id']),
            symbol=symbols.get(e['data']['order_id']),
            quantity=e['data']['quantity'],
            price=e['data']['price'],
            fee=e['data']['fee'],
        )
        for e in fills
    ]


# Trading days per year used to annualise the Sharpe ratio of daily returns.
TRADING_DAYS_PER_YEAR = 252
# Fewer daily returns than this make a Sharpe ratio too noisy to rely on; the result
# page says so next to the figure.
RELIABLE_SHARPE_RETURNS = 20


def round_trips(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Pair fills into round trips with the ledger's own average-cost rule.

    A round trip opens with the fill that takes the position away from zero shares,
    long or short, and closes with the fill that brings it back to zero; a fill that
    crosses zero closes one trip and opens the next. P&L follows domain.ledger.actions
    exactly: a long's cost includes buy fees and a partial sale takes
    ``cost * shares // held``; a short's cost is its proceeds after fees, released in
    proportion as it is covered. So the trips' P&L adds up to the ledger's realized P&L.

    Returns ``trips`` (closed: open_ns, close_ns, pnl), ``realized_pnl`` (every
    realization, including those inside a trip still open at the end) and ``open``.
    """
    book = dict(held=0, cost=0, realized=0, opened=0, trip=0)
    trips: List[Dict[str, int]] = []

    def realize(pnl: int, time_ns: int) -> None:
        book['realized'] += pnl
        book['trip'] += pnl
        if book['held'] == 0:
            trips.append(dict(open_ns=book['opened'], close_ns=time_ns, pnl=book['trip']))

    def open_if_flat(time_ns: int) -> None:
        if book['held'] == 0:
            book['opened'], book['trip'] = time_ns, 0

    for row in sorted(rows, key=lambda r: r['time_ns']):
        price, quantity, fee, stamp = row['price'], row['quantity'], row['fee'], row['time_ns']
        held = book['held']
        if row['side'] == 'buy' and held < 0:
            covering = min(quantity, -held)
            fee_cover = fee * covering // quantity
            cost = book['cost']
            released = -cost if covering == -held else -cost * covering // -held
            book['held'], book['cost'] = held + covering, cost + released
            realize(released - covering * price - fee_cover, stamp)
            if quantity > covering:
                open_if_flat(stamp)
                book['held'] += quantity - covering
                book['cost'] += (quantity - covering) * price + fee - fee_cover
        elif row['side'] == 'buy':
            open_if_flat(stamp)
            book['held'], book['cost'] = held + quantity, book['cost'] + quantity * price + fee
        elif quantity <= held:
            cost = book['cost']
            allocated = cost if quantity == held else cost * quantity // held
            book['held'], book['cost'] = held - quantity, cost - allocated
            realize(quantity * price - fee - allocated, stamp)
        else:
            closing = max(0, held)
            fee_close = fee * closing // quantity
            if closing:
                cost = book['cost']
                book['held'], book['cost'] = 0, 0
                realize(closing * price - fee_close - cost, stamp)
            open_if_flat(stamp)
            opening = quantity - closing
            book['held'] -= opening
            book['cost'] -= opening * price - (fee - fee_close)
    return dict(trips=trips, realized_pnl=book['realized'], open=book['held'] != 0)


def daily_returns(
    initial_cash: int, rows: List[Dict[str, Any]], closes: List[List[int]]
) -> Optional[List[float]]:
    """Daily equity returns of a one-symbol account, or None once equity is not positive.

    Equity at each day's last trade price (``closes``: [day, time_ns, price]) is cash
    plus shares held. The first return is measured from the starting cash.
    """
    cash, held, index, previous = initial_cash, 0, 0, initial_cash
    ordered = sorted(rows, key=lambda r: r['time_ns'])
    returns: List[float] = []
    for _, time_ns, price in closes:
        while index < len(ordered) and ordered[index]['time_ns'] <= time_ns:
            row = ordered[index]
            notional = row['quantity'] * row['price']
            sign = 1 if row['side'] == 'buy' else -1
            cash -= sign * notional + row['fee']
            held += sign * row['quantity']
            index += 1
        equity = cash + held * price
        if previous <= 0:
            return None
        returns.append(equity / previous - 1)
        previous = equity
    return returns


def sharpe_ratio(returns: Optional[List[float]]) -> Optional[float]:
    """Annualised Sharpe ratio of daily returns, or None with fewer than two or no spread.

    Risk-free rate zero, sample standard deviation, scaled by the square root of 252.
    """
    from statistics import mean, stdev

    if returns is None or len(returns) < 2 or stdev(returns) == 0:
        return None
    return mean(returns) / stdev(returns) * TRADING_DAYS_PER_YEAR**0.5


def sortino_ratio(returns: Optional[List[float]]) -> Optional[float]:
    """Annualised Sortino ratio of daily returns, or None with fewer than two or no loss.

    Mean return over the downside deviation: the root mean square of every return's
    shortfall below zero (gains count as zero), over all returns. Target and risk-free
    rate zero, scaled by the square root of 252.
    """
    from statistics import mean

    if returns is None or len(returns) < 2:
        return None
    downside = (sum(min(r, 0.0) ** 2 for r in returns) / len(returns)) ** 0.5
    if downside == 0:
        return None
    return mean(returns) / downside * TRADING_DAYS_PER_YEAR**0.5


def daily_sharpe(
    initial_cash: int, rows: List[Dict[str, Any]], closes: List[List[int]]
) -> Optional[float]:
    """Annualised Sharpe ratio of a one-symbol account's daily returns (daily_returns)."""
    return sharpe_ratio(daily_returns(initial_cash, rows, closes))


def _apply_fill(row: Dict[str, Any], cash: int, held: Dict[Any, int]) -> int:
    """Apply one fill to cash and the per-symbol holdings; return the new cash."""
    sign = 1 if row['side'] == 'buy' else -1
    held[row.get('symbol')] = held.get(row.get('symbol'), 0) + sign * row['quantity']
    return cash - sign * row['quantity'] * row['price'] - row['fee']


def portfolio_daily_returns(
    initial_cash: int, rows: List[Dict[str, Any]], closes: Dict[str, List[List[int]]]
) -> Tuple[int, Optional[List[float]]]:
    """(days counted, daily returns or None) of an account trading several symbols.

    Each day the account is cash plus every holding at its symbol's close that day; a
    symbol with no close that day keeps its last close. Fills up to the day's latest close
    count. The first return is measured from the starting cash. Returns are None when a
    held symbol has no close yet or equity is not positive.
    """
    by_day: Dict[int, Dict[str, List[int]]] = {}
    for symbol, values in closes.items():
        for day, time_ns, price in values:
            by_day.setdefault(day, {})[symbol] = [time_ns, price]
    ordered = sorted(rows, key=lambda r: r['time_ns'])
    cash, index, previous = initial_cash, 0, initial_cash
    held: Dict[Any, int] = {}
    last: Dict[str, int] = {}
    returns: List[float] = []
    for day in sorted(by_day):
        cutoff = max(time_ns for time_ns, _ in by_day[day].values())
        last.update({symbol: price for symbol, (_, price) in by_day[day].items()})
        while index < len(ordered) and ordered[index]['time_ns'] <= cutoff:
            cash = _apply_fill(ordered[index], cash, held)
            index += 1
        if any(quantity and symbol not in last for symbol, quantity in held.items()):
            return len(returns), None
        equity = cash + sum(q * last[s] for s, q in held.items() if q)
        if previous <= 0:
            return len(returns), None
        returns.append(equity / previous - 1)
        previous = equity
    return len(returns), returns


def portfolio_daily_sharpe(
    initial_cash: int, rows: List[Dict[str, Any]], closes: Dict[str, List[List[int]]]
) -> Tuple[int, Optional[float]]:
    """(daily returns, annualised Sharpe ratio) of an account trading several symbols."""
    days, returns = portfolio_daily_returns(initial_cash, rows, closes)
    return days, sharpe_ratio(returns)


def trade_statistics(view: View, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Sharpe and Sortino ratios, win rate, turnover and average holding time of a run.

    Defined in docs/user-guide/results.md. Each value is None when it cannot be
    computed: a run that started with shares held, no closed round trip, or fewer than
    two daily returns. ``realized_pnl`` is recomputed from the fills as a check.
    """
    initial = (view.get('run') or {}).get('initial_snapshot') or {}
    empty = dict(
        sharpe_ratio=None,
        sortino_ratio=None,
        sharpe_returns=0,
        sharpe_reliable=False,
        win_rate=None,
        round_trips=0,
        winning_trips=0,
        turnover=None,
        average_holding_ns=None,
        realized_pnl=None,
    )
    if not initial or initial['positions'] or any(r['side'] not in ('buy', 'sell') for r in rows):
        return empty
    # Each symbol is its own position; round trips are paired per symbol and added up.
    by_symbol: Dict[Any, List[Dict[str, Any]]] = {}
    for row in rows:
        by_symbol.setdefault(row.get('symbol'), []).append(row)
    paired = [round_trips(group) for group in by_symbol.values()]
    trips = [trip for result in paired for trip in result['trips']]
    wins = sum(1 for t in trips if t['pnl'] > 0)
    traded = sum(r['quantity'] * r['price'] for r in rows)
    markets = view.get('markets') or {}
    daily: Optional[List[float]]
    if markets:
        closes = {s: m.get('daily_closes') or [] for s, m in markets.items()}
        days, daily = portfolio_daily_returns(initial['cash'], rows, closes)
    else:
        # Older runs on several symbols saved one symbol's closes: no Sharpe for them.
        several = len(by_symbol) > 1
        days = 0 if several else len(view.get('daily_closes') or [])
        daily = (
            None
            if several
            else daily_returns(initial['cash'], rows, view.get('daily_closes') or [])
        )
    # One return per recorded day; the first is measured from the starting cash.
    returns = days
    return dict(
        sharpe_returns=returns,
        sharpe_reliable=returns >= RELIABLE_SHARPE_RETURNS,
        sharpe_ratio=sharpe_ratio(daily),
        sortino_ratio=sortino_ratio(daily),
        win_rate=wins / len(trips) if trips else None,
        round_trips=len(trips),
        winning_trips=wins,
        turnover=traded / initial['cash'] if initial['cash'] > 0 else None,
        average_holding_ns=(
            sum(t['close_ns'] - t['open_ns'] for t in trips) // len(trips) if trips else None
        ),
        realized_pnl=sum(result['realized_pnl'] for result in paired),
    )


def performance(view: View) -> Dict[str, Any]:
    """Describe a saved run from its complete fills and archived bars.

    Equity is cash plus the position marked at each archived bar's close. It is a
    display estimate over the archived window, not a ledger or a liquidation value.
    Live views publish only recent events, so they receive fills without a curve.
    """
    rows = fill_rows(view)
    result = dict(
        fills=rows,
        series=None,
        initial_cash=None,
        max_drawdown=None,
        max_drawdown_ratio=None,
        total_return=None,
        exposure=None,
        traded_shares=sum(r['quantity'] for r in rows),
        traded_notional=sum(r['quantity'] * r['price'] for r in rows),
    )
    if view.get('fills') is not None:
        result.update(trade_statistics(view, rows))
    initial = (view.get('run') or {}).get('initial_snapshot') or {}
    bars = view.get('bars') or []
    markets = view.get('markets') or {}
    if (
        view.get('fills') is None
        or not initial
        or initial['positions']
        or not (bars or markets)
        or any(r['side'] not in ('buy', 'sell') for r in rows)
        # Older runs on several symbols archived one symbol's bars: no curve for them.
        or (not markets and len({r.get('symbol') for r in rows}) > 1)
    ):
        return result
    if markets:
        series = portfolio_series(
            initial['cash'], rows, {s: m.get('bars') or [] for s, m in markets.items()}
        )
        if series:
            result.update(
                initial_cash=initial['cash'], **_curve_figures(initial['cash'], series, rows)
            )
        return result
    cash, quantity, index = initial['cash'], 0, 0
    rows = sorted(rows, key=lambda r: r['time_ns'])

    def apply(until: Optional[int]) -> None:
        nonlocal cash, quantity, index
        while index < len(rows) and (until is None or rows[index]['time_ns'] < until):
            row = rows[index]
            notional = row['quantity'] * row['price']
            if row['side'] == 'buy':
                cash -= notional + row['fee']
                quantity += row['quantity']
            else:
                cash += notional - row['fee']
                quantity -= row['quantity']
            index += 1

    apply(bars[0]['start_ns'])
    series = [[bars[0]['start_ns'], cash + quantity * bars[0]['open']]]
    for position, bar in enumerate(bars):
        last = position == len(bars) - 1
        apply(None if last else bar['end_ns'])
        stamp = bar['end_ns'] if bar.get('complete', True) else bar['observed_through_ns']
        series.append([stamp, cash + quantity * bar['close']])
    result.update(initial_cash=initial['cash'], **_curve_figures(initial['cash'], series, rows))
    return result


def _curve_figures(
    initial_cash: int, series: List[List[int]], rows: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """Drawdown, total return and exposure of an equity curve built from ``rows``.

    Total return is the last point's equity over the starting cash, minus one. Exposure
    is the share of the curve's points after the first at which any position is open,
    counting the fills before that point (every fill at the last point), as the curve does.
    """
    ordered = sorted(rows, key=lambda r: r['time_ns'])
    held: Dict[Any, int] = {}
    index, exposed = 0, 0
    points = series[1:]
    for position, (stamp, _) in enumerate(points):
        last = position == len(points) - 1
        while index < len(ordered) and (last or ordered[index]['time_ns'] < stamp):
            _apply_fill(ordered[index], 0, held)
            index += 1
        exposed += any(held.values())
    return dict(
        _drawdown(series),
        total_return=series[-1][1] / initial_cash - 1 if initial_cash > 0 else None,
        exposure=exposed / len(points) if points else None,
    )


def _drawdown(series: List[List[int]]) -> Dict[str, Any]:
    """The series with its largest fall from a previous peak, in money and as a ratio."""
    peak, drawdown, ratio = series[0][1], 0, 0.0
    for _, equity in series:
        peak = max(peak, equity)
        if peak - equity > drawdown:
            drawdown, ratio = peak - equity, (peak - equity) / peak if peak else 0.0
    return dict(series=series, max_drawdown=drawdown, max_drawdown_ratio=ratio)


def portfolio_series(
    initial_cash: int, rows: List[Dict[str, Any]], bars: Dict[str, List[Dict[str, Any]]]
) -> Optional[List[List[int]]]:
    """[time_ns, equity] of an account on several symbols over their archived bars.

    The curve starts when every symbol has a bar, at the latest first bar's open. At
    each bar's end, equity is cash plus every holding at its symbol's latest close (a
    symbol keeps its last close until its next bar ends). Fills before that time count.
    None when a symbol has no archived bar.
    """
    if not bars or any(not values for values in bars.values()):
        return None

    def end(bar: Dict[str, Any]) -> int:
        return int(bar['end_ns'] if bar.get('complete', True) else bar['observed_through_ns'])

    first = max(values[0]['start_ns'] for values in bars.values())
    price = {}
    for symbol, values in bars.items():
        started = [b for b in values if b['start_ns'] <= first]
        price[symbol] = (
            started[-1]['open'] if started[-1]['start_ns'] == first else started[-1]['close']
        )
    events = sorted(
        (end(b), symbol, b['close'])
        for symbol, values in bars.items()
        for b in values
        if end(b) > first
    )
    ordered = sorted(rows, key=lambda r: r['time_ns'])
    cash, index = initial_cash, 0
    held: Dict[Any, int] = {}

    def apply(until: Optional[int]) -> None:
        nonlocal cash, index
        while index < len(ordered) and (until is None or ordered[index]['time_ns'] < until):
            cash = _apply_fill(ordered[index], cash, held)
            index += 1

    def equity() -> int:
        return cash + sum(q * price[s] for s, q in held.items() if q and s in price)

    apply(first)
    series = [[first, equity()]]
    for position, (stamp, symbol, close) in enumerate(events):
        apply(None if position == len(events) - 1 else stamp)
        price[symbol] = close
        if series[-1][0] == stamp:
            series[-1][1] = equity()
        else:
            series.append([stamp, equity()])
    return series
