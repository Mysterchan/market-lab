"""Portfolio construction: from signals or returns to weights, share targets and orders.

Pure functions over plain lists and dictionaries, so they work in a notebook, in a
model file's own environment (copy this module) and in an Agent's local tool.

- Weights: ``equal_weights``, ``inverse_volatility``, ``risk_parity``,
  ``minimum_variance`` (long only) and ``signal_weights`` (scale signals to a gross).
- Risk: ``volatility_target`` scales weights to a target annual volatility, and
  ``apply_limits`` caps each weight, the gross and the net exposure.
- Execution: ``target_shares`` turns weights into whole shares at given prices, and
  ``rebalance_orders`` lists the buys and sells that move holdings to the targets.

Money and prices are integers in ten-thousandths of a dollar, as everywhere in Market
Lab; returns are decimals per period.
"""

from __future__ import annotations

from math import sqrt
from typing import Dict, List, Mapping, Optional, Sequence

Weights = Dict[str, float]


def _covariance(returns: Mapping[str, Sequence[float]]) -> Dict[str, Dict[str, float]]:
    names = list(returns)
    length = min(len(returns[n]) for n in names)
    if length < 2:
        raise ValueError('need at least two returns per asset')
    means = {n: sum(returns[n][:length]) / length for n in names}
    return {
        a: {
            b: sum((returns[a][i] - means[a]) * (returns[b][i] - means[b]) for i in range(length))
            / (length - 1)
            for b in names
        }
        for a in names
    }


def equal_weights(symbols: Sequence[str]) -> Weights:
    """1/n in each symbol."""
    return {symbol: 1 / len(symbols) for symbol in symbols} if symbols else {}


def inverse_volatility(returns: Mapping[str, Sequence[float]]) -> Weights:
    """Weights proportional to 1/volatility, summing to one."""
    covariance = _covariance(returns)
    inverse = {n: 1 / sqrt(covariance[n][n]) if covariance[n][n] > 0 else 0.0 for n in returns}
    total = sum(inverse.values())
    return {n: v / total for n, v in inverse.items()} if total else equal_weights(list(returns))


def risk_parity(
    returns: Mapping[str, Sequence[float]], *, iterations: int = 500, tolerance: float = 1e-10
) -> Weights:
    """Long-only weights where each asset contributes equally to portfolio variance.

    Solved by the multiplicative fixed point w_i <- w_i * (1/n) / (w_i (Σw)_i / w'Σw),
    started from inverse volatility.
    """
    covariance = _covariance(returns)
    names = list(returns)
    weights = inverse_volatility(returns)
    target = 1 / len(names)
    for _ in range(iterations):
        marginal = {a: sum(covariance[a][b] * weights[b] for b in names) for a in names}
        variance = sum(weights[a] * marginal[a] for a in names)
        if variance <= 0:
            break
        updated = {
            a: (
                weights[a] * sqrt(target / (weights[a] * marginal[a] / variance))
                if weights[a] * marginal[a] > 0
                else weights[a]
            )
            for a in names
        }
        total = sum(updated.values())
        updated = {a: v / total for a, v in updated.items()}
        if max(abs(updated[a] - weights[a]) for a in names) < tolerance:
            return updated
        weights = updated
    return weights


def minimum_variance(
    returns: Mapping[str, Sequence[float]], *, iterations: int = 2000, step: Optional[float] = None
) -> Weights:
    """Long-only, fully invested minimum-variance weights by projected gradient descent."""
    covariance = _covariance(returns)
    names = list(returns)
    weights = equal_weights(names)
    largest = max(sum(abs(covariance[a][b]) for b in names) for a in names) or 1.0
    rate = step if step is not None else 1 / largest

    def project(values: Dict[str, float]) -> Dict[str, float]:
        # Euclidean projection onto the simplex (weights >= 0 summing to one).
        ordered = sorted(values.values(), reverse=True)
        cumulative, theta = 0.0, 0.0
        for index, value in enumerate(ordered, 1):
            cumulative += value
            candidate = (cumulative - 1) / index
            if value - candidate > 0:
                theta = candidate
        return {a: max(v - theta, 0.0) for a, v in values.items()}

    for _ in range(iterations):
        gradient = {a: 2 * sum(covariance[a][b] * weights[b] for b in names) for a in names}
        weights = project({a: weights[a] - rate * gradient[a] for a in names})
    return weights


def signal_weights(signals: Mapping[str, float], *, gross: float = 1.0) -> Weights:
    """Scale signals (any sign) so absolute weights sum to ``gross``; zeros stay zero."""
    total = sum(abs(v) for v in signals.values())
    return (
        {s: gross * v / total for s, v in signals.items()} if total else dict.fromkeys(signals, 0.0)
    )


def portfolio_volatility(
    weights: Mapping[str, float], returns: Mapping[str, Sequence[float]], periods_per_year: float
) -> float:
    """Annualised volatility of a weighted portfolio from per-period returns."""
    covariance = _covariance({s: returns[s] for s in weights})
    variance = sum(weights[a] * weights[b] * covariance[a][b] for a in weights for b in weights)
    return sqrt(max(variance, 0.0) * periods_per_year)


def volatility_target(
    weights: Mapping[str, float],
    returns: Mapping[str, Sequence[float]],
    *,
    target: float,
    periods_per_year: float = 252,
    max_leverage: float = 1.0,
) -> Weights:
    """Scale weights so the portfolio's annual volatility is ``target`` (capped leverage)."""
    current = portfolio_volatility(weights, returns, periods_per_year)
    if current <= 0:
        return dict(weights)
    gross = sum(abs(v) for v in weights.values())
    scale = target / current
    if gross * scale > max_leverage:
        scale = max_leverage / gross
    return {s: v * scale for s, v in weights.items()}


def apply_limits(
    weights: Mapping[str, float],
    *,
    max_weight: Optional[float] = None,
    max_gross: Optional[float] = None,
    max_net: Optional[float] = None,
) -> Weights:
    """Cap each absolute weight, then scale down to the gross and net limits."""
    capped = {
        s: (max(-max_weight, min(max_weight, v)) if max_weight is not None else v)
        for s, v in weights.items()
    }
    gross = sum(abs(v) for v in capped.values())
    if max_gross is not None and gross > max_gross:
        capped = {s: v * max_gross / gross for s, v in capped.items()}
    net = sum(capped.values())
    if max_net is not None and abs(net) > max_net:
        # Shrink the side that pushes the net beyond its limit.
        side = 1 if net > 0 else -1
        excess = abs(net) - max_net
        exposure = sum(v for v in capped.values() if v * side > 0) * side
        if exposure > 0:
            factor = (exposure - excess) / exposure
            capped = {s: v * factor if v * side > 0 else v for s, v in capped.items()}
    return capped


def target_shares(
    weights: Mapping[str, float],
    prices: Mapping[str, int],
    equity: int,
    *,
    lot: int = 1,
) -> Dict[str, int]:
    """Whole shares (in multiples of ``lot``) closest below each weight of ``equity``."""
    shares: Dict[str, int] = {}
    for symbol, weight in weights.items():
        price = prices[symbol]
        if price <= 0:
            raise ValueError(f'price of {symbol} must be positive')
        raw = weight * equity / price
        lots = int(abs(raw) // lot) * lot
        shares[symbol] = lots if raw >= 0 else -lots
    return shares


def rebalance_orders(
    current: Mapping[str, int],
    target: Mapping[str, int],
    *,
    min_shares: int = 1,
) -> List[Dict[str, object]]:
    """Buys and sells that move ``current`` holdings to ``target``, sells first.

    Each order is ``{'symbol', 'side', 'quantity'}``; changes smaller than
    ``min_shares`` are skipped to avoid paying costs for noise.
    """
    orders = []
    for symbol in sorted(set(current) | set(target)):
        change = target.get(symbol, 0) - current.get(symbol, 0)
        if abs(change) >= min_shares and change:
            orders.append(
                dict(symbol=symbol, side='buy' if change > 0 else 'sell', quantity=abs(change))
            )
    return sorted(orders, key=lambda order: order['side'] != 'sell')
