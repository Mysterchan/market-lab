"""PnL attribution of one run: alpha, beta, factor exposure and trading costs.

Returns are the run's per-bar equity returns (``evaluation.run_period_returns``). The
market factor is buying and holding the traded symbols in equal weights over the same
bars; further factors (for example Fama-French daily returns) can be supplied as
series aligned with those periods. The regression is ordinary least squares,

    r_run = alpha + sum_k beta_k * f_k + e,

with classical standard errors. Alpha is per period and annualised by the bar length.
Costs split the money paid on fills into commission, borrow and market impact (the
difference between the impacted fill price and the quoted price, when an impact model
was used). Spread paid is not separated: fills record the price paid, not the mid.
"""

from __future__ import annotations

from math import sqrt
from statistics import median
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .reports import Report

# Market time in one trading day and one year, for annualising per-bar figures.
SECONDS_PER_TRADING_DAY = 6.5 * 3600
TRADING_DAYS_PER_YEAR = 252
# Fewer periods than this leave the regression too noisy to report.
MIN_PERIODS = 5


def _solve(matrix: List[List[float]], vector: List[float]) -> Optional[List[float]]:
    """Solve a small linear system by Gaussian elimination; None when singular."""
    size = len(vector)
    rows = [list(matrix[i]) + [vector[i]] for i in range(size)]
    for column in range(size):
        pivot = max(range(column, size), key=lambda r: abs(rows[r][column]))
        if abs(rows[pivot][column]) < 1e-14:
            return None
        rows[column], rows[pivot] = rows[pivot], rows[column]
        for row in range(size):
            if row != column:
                factor = rows[row][column] / rows[column][column]
                rows[row] = [a - factor * b for a, b in zip(rows[row], rows[column])]
    return [rows[i][size] / rows[i][i] for i in range(size)]


def _inverse(matrix: List[List[float]]) -> Optional[List[List[float]]]:
    size = len(matrix)
    columns = []
    for index in range(size):
        unit = [1.0 if i == index else 0.0 for i in range(size)]
        column = _solve(matrix, unit)
        if column is None:
            return None
        columns.append(column)
    return [[columns[j][i] for j in range(size)] for i in range(size)]


def regress(returns: Sequence[float], factors: Mapping[str, Sequence[float]]) -> Dict[str, Any]:
    """OLS of ``returns`` on an intercept and each factor; coefficients with t-statistics.

    Returns ``{'alpha', 'alpha_t', 'betas': {name: {'beta', 't'}}, 'r_squared',
    'residual_volatility', 'periods'}``, or ``{'periods', 'reason'}`` when there are
    too few periods or the factors are collinear.
    """
    names = list(factors)
    count = len(returns)
    if count < MIN_PERIODS or count <= len(names) + 1:
        return dict(periods=count, reason='too few periods for a regression')
    if any(len(factors[name]) != count for name in names):
        return dict(periods=count, reason='factor series do not match the run periods')
    design = [[1.0] + [float(factors[name][i]) for name in names] for i in range(count)]
    xtx = [
        [sum(row[a] * row[b] for row in design) for b in range(len(names) + 1)]
        for a in range(len(names) + 1)
    ]
    xty = [sum(row[a] * returns[i] for i, row in enumerate(design)) for a in range(len(names) + 1)]
    inverse = _inverse(xtx)
    if inverse is None:
        return dict(periods=count, reason='factors are collinear or constant')
    coefficients = [sum(inverse[a][b] * xty[b] for b in range(len(xty))) for a in range(len(xty))]
    fitted = [sum(c * x for c, x in zip(coefficients, row)) for row in design]
    residuals = [r - f for r, f in zip(returns, fitted)]
    freedom = count - len(coefficients)
    variance = sum(e * e for e in residuals) / freedom
    mean = sum(returns) / count
    total = sum((r - mean) ** 2 for r in returns)
    errors = [sqrt(max(variance * inverse[i][i], 0.0)) for i in range(len(coefficients))]

    def t(index: int) -> Optional[float]:
        return coefficients[index] / errors[index] if errors[index] > 0 else None

    return dict(
        periods=count,
        alpha=coefficients[0],
        alpha_t=t(0),
        betas={name: dict(beta=coefficients[i + 1], t=t(i + 1)) for i, name in enumerate(names)},
        r_squared=1 - sum(e * e for e in residuals) / total if total > 0 else None,
        residual_volatility=sqrt(variance),
    )


def _bars(view: Mapping[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    markets = view.get('markets') or {}
    if markets:
        return {symbol: market.get('bars') or [] for symbol, market in markets.items()}
    return {'': view.get('bars') or []}


def _bar_end(bar: Dict[str, Any]) -> int:
    return int(bar['end_ns'] if bar.get('complete', True) else bar['observed_through_ns'])


def market_returns(view: Mapping[str, Any], stamps: Sequence[int]) -> Optional[List[float]]:
    """Equal-weight buy-and-hold returns of the run's symbols between ``stamps``.

    A symbol's price at a stamp is its latest bar close ending by then, or its first
    open before its first bar ends, the convention of ``metrics.portfolio_series``.
    None when a symbol has no bars.
    """
    bars = _bars(view)
    if not bars or any(not values for values in bars.values()):
        return None
    per_symbol: List[List[float]] = []
    for values in bars.values():
        prices = []
        for stamp in stamps:
            closed = [b for b in values if _bar_end(b) <= stamp]
            prices.append(float(closed[-1]['close'] if closed else values[0]['open']))
        if any(p <= 0 for p in prices):
            return None
        per_symbol.append([b / a - 1 for a, b in zip(prices, prices[1:])])
    return [sum(v[i] for v in per_symbol) / len(per_symbol) for i in range(len(stamps) - 1)]


def periods_per_year(stamps: Sequence[int]) -> Optional[float]:
    """Bars per trading year, from the median bar length in market time."""
    gaps = [b - a for a, b in zip(stamps, stamps[1:]) if b > a]
    if not gaps:
        return None
    seconds = median(gaps) / 1e9
    if seconds >= 20 * 3600:  # daily or longer bars: count trading days
        return TRADING_DAYS_PER_YEAR / max(1.0, round(seconds / 86400))
    return TRADING_DAYS_PER_YEAR * SECONDS_PER_TRADING_DAY / seconds


def open_short_borrow(report: Report, view: Mapping[str, Any]) -> int:
    """Borrow accrued on shorts still open when the run ended, in money units.

    The ledger charges borrow when a short is covered; a short open at the end has
    accrued borrow that no fill paid. This replays the run's fills with the same rule
    (execution.costs) and accrues each open short from its share-weighted opening time
    to the run's last event, at the run's ``borrow_bps_per_year``.
    """
    from sim_platform.execution import ExecutionConfig
    from sim_platform.execution.costs import borrow_fee, charge_borrow

    execution = ((report.get('run') or {}).get('config') or {}).get('execution') or {}
    rate = int(execution.get('borrow_bps_per_year') or 0)
    events = report.get('run_events') or []
    if not rate or not events:
        return 0
    config = ExecutionConfig(borrow_bps_per_year=rate)
    sides = {
        o['order_id']: (o['side'], o.get('symbol'))
        for o in (view.get('account') or {}).get('orders', [])
    }
    positions: Dict[str, Any] = {}
    shorts: Dict[str, int] = {}
    for event in events:
        if event.get('kind') == 'fill' and event['data']['order_id'] in sides:
            fill = dict(event['data'])
            side, symbol = sides[fill['order_id']]
            before = positions.get(symbol, (0, 0))[0]
            charge_borrow(config, positions, sides, shorts, [fill], int(event['time_ns']))
            held, cost = positions[symbol]
            added = max(0, -held) - max(0, -before)
            if side == 'sell' and added > 0:
                # The ledger's short proceeds are net of the sale's fee, pro rata.
                positions[symbol] = (held, cost + int(fill['fee']) * added // fill['quantity'])
    end_ns = max(int(e['time_ns']) for e in events if e.get('time_ns') is not None)
    return sum(
        borrow_fee(config, -cost, shorts.get(symbol, end_ns), end_ns)
        for symbol, (held, cost) in positions.items()
        if held < 0
    )


def trading_costs(report: Report, view: Mapping[str, Any]) -> Dict[str, Any]:
    """Commission, borrow and market impact paid on fills, in money units and bps.

    ``borrow`` includes ``borrow_accrued``: borrow on shorts still open at the end.
    """
    events = report.get('run_events') or []
    orders = {o['order_id']: o['side'] for o in (view.get('account') or {}).get('orders', [])}
    fees = borrow = impact = notional = 0
    for event in events:
        if event.get('kind') != 'fill':
            continue
        data = event['data']
        borrow += int(data.get('borrow_fee', 0))
        fees += int(data['fee']) - int(data.get('borrow_fee', 0))
        notional += int(data['quantity']) * int(data['price'])
        if 'quoted_price' in data:
            sign = 1 if orders.get(data['order_id']) == 'buy' else -1
            impact += (
                sign * (int(data['price']) - int(data['quoted_price'])) * int(data['quantity'])
            )
    accrued = open_short_borrow(report, view)
    borrow += accrued
    total = fees + borrow + impact

    def bps(value: int) -> Optional[float]:
        return value / notional * 1e4 if notional else None

    return dict(
        commission=fees,
        borrow=borrow,
        borrow_accrued=accrued,
        impact=impact,
        total=total,
        traded_notional=notional,
        commission_bps=bps(fees),
        borrow_bps=bps(borrow),
        impact_bps=bps(impact),
        total_bps=bps(total),
    )


def pnl_attribution(
    report: Report, factors: Optional[Mapping[str, Sequence[float]]] = None
) -> Dict[str, Any]:
    """Alpha, beta to the traded market, other factor exposures and costs of one run.

    Args:
        report: A saved run report (``Platform.report_data``).
        factors: Optional extra factor return series, one value per run period (the
            periods between consecutive points of the run's equity curve).

    Returns:
        ``{'periods', 'periods_per_year', 'market', 'factors', 'costs', 'pnl'}``:
        ``market`` and ``factors`` are regression results (see ``regress``) with
        ``alpha_annual`` added; ``costs`` comes from ``trading_costs``; ``pnl`` holds
        net PnL and gross PnL before costs, in money units.
    """
    from .evaluation import run_period_returns
    from .metrics import performance
    from .views import session_view

    view = session_view(dict(mode='saved', report=report))
    figures = performance(view)
    series = figures.get('series')
    returns = run_period_returns(report)
    costs = trading_costs(report, view)
    result: Dict[str, Any] = dict(periods=len(returns or []), costs=costs)
    if series and figures.get('initial_cash'):
        net = series[-1][1] - figures['initial_cash']
        result['pnl'] = dict(net=net, gross=net + costs['total'])
    if not returns or not series:
        result['reason'] = 'the run has no equity curve'
        return result
    stamps = [int(point[0]) for point in series]
    yearly = periods_per_year(stamps)
    result['periods_per_year'] = yearly
    market = market_returns(view, stamps)

    def annualise(fit: Dict[str, Any]) -> Dict[str, Any]:
        if 'alpha' in fit and yearly:
            fit['alpha_annual'] = fit['alpha'] * yearly
        return fit

    if market is not None:
        result['market'] = annualise(regress(returns, dict(market=market)))
    if factors:
        combined = dict(factors)
        if market is not None:
            combined = dict(market=market, **combined)
        result['factors'] = annualise(regress(returns, combined))
    return result
