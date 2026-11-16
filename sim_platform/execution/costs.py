"""Deterministic execution costs: order latency and square-root market impact.

Both are pure functions of the run's ExecutionConfig and recorded order data, so a
Replay that applies the same records reproduces the same eligibility times and prices.
"""

from __future__ import annotations

from hashlib import sha256
import math
from statistics import NormalDist
from typing import Any, Dict, List, Sequence, Tuple

from .models import MAX_LATENCY_MULTIPLE, ExecutionConfig, Level

BASIS_POINTS = 10_000


def order_delay(config: ExecutionConfig, order_id: str) -> int:
    """Nanoseconds from acceptance until the order may first match.

    The fixed latency plus a jitter in [0, latency_jitter_ns], or a lognormal delay with
    median latency_ns when latency_sigma_milli is set. Both draw from the order ID's
    SHA-256 digest, so the same order always gets the same delay.
    """
    draw = int.from_bytes(sha256(order_id.encode()).digest()[:8], 'big')
    if config.latency_sigma_milli:
        # A uniform draw strictly inside (0, 1), turned into a standard normal value.
        # 53 bits fit a float exactly, so the value never rounds to 0 or 1.
        z = NormalDist().inv_cdf(((draw >> 11) + 0.5) / 2**53)
        median = config.latency_ns
        delay = median * math.exp(config.latency_sigma_milli / 1000 * z)
        return min(int(round(delay)), median * MAX_LATENCY_MULTIPLE)
    if not config.latency_jitter_ns:
        return config.latency_ns
    return config.latency_ns + draw % (config.latency_jitter_ns + 1)


def impacted_price(
    config: ExecutionConfig,
    side: str,
    price: int,
    quantity: int,
    displayed: Sequence[Level],
    limit: int,
) -> int:
    """The price a fill pays after square-root market impact.

    The impact is ``impact_bps * sqrt(quantity / displayed shares on the opposite side)``
    basis points against the trader, rounded against the trader to a whole money unit
    and never beyond ``limit``. With ``impact_bps`` 0 the quoted price is returned.

    Args:
        config: The run's execution settings.
        side: 'buy' or 'sell'.
        price: The displayed price level being taken.
        quantity: Shares taken at that level.
        displayed: The opposite side's displayed (price, shares) levels before the fill.
        limit: The order's limit price.
    """
    if not config.impact_bps:
        return price
    depth = sum(size for _, size in displayed)
    fraction = min(1.0, quantity / depth) if depth > 0 else 1.0
    move = price * config.impact_bps * math.sqrt(fraction) / BASIS_POINTS
    if side == 'buy':
        return min(limit, price + math.ceil(move))
    return max(limit, price - math.ceil(move))


NS_PER_YEAR = 365 * 86_400 * 1_000_000_000


def borrow_fee(config: ExecutionConfig, proceeds: int, opened_ns: int, closed_ns: int) -> int:
    """Borrow cost of covering a short whose sale raised ``proceeds`` money units.

    Charged at ``borrow_bps_per_year`` for the market time between the short's
    (share-weighted) opening and the cover, rounded up against the trader.
    """
    if not config.borrow_bps_per_year or proceeds <= 0 or closed_ns <= opened_ns:
        return 0
    numerator = proceeds * config.borrow_bps_per_year * (closed_ns - opened_ns)
    return -(-numerator // (BASIS_POINTS * NS_PER_YEAR))


def charge_borrow(
    config: ExecutionConfig,
    positions: Dict[str, Tuple[int, int]],
    sides: Dict[str, Tuple[str, str]],
    shorts: Dict[str, int],
    fills: List[Dict[str, Any]],
    time_ns: int,
) -> None:
    """Add borrow fees to covering fills and track when each short opened, in place.

    ``positions`` maps symbol to (shares, cost) before these fills, ``sides`` maps an
    order ID to (side, symbol) and ``shorts`` maps symbol to the short's share-weighted
    opening time. Fills are applied in order, so several fills of one event compose.
    """
    for fill in fills:
        side, symbol = sides[fill['order_id']]
        held, cost = positions.get(symbol, (0, 0))
        quantity = fill['quantity']
        if side == 'buy' and held < 0:
            covered = min(quantity, -held)
            proceeds = -cost * covered // -held
            fee = borrow_fee(config, proceeds, shorts.get(symbol, time_ns), time_ns)
            if fee:
                fill['fee'] += fee
                fill['borrow_fee'] = fee
            cost += proceeds
            held += covered
            if held == 0:
                shorts.pop(symbol, None)
            positions[symbol] = (held + quantity - covered, cost)
        elif side == 'sell':
            short_before = max(0, -held)
            short_after = max(0, quantity - held) if held >= 0 else short_before + quantity
            added = short_after - short_before
            if added:
                opened = shorts.get(symbol, time_ns)
                shorts[symbol] = (short_before * opened + added * time_ns) // short_after
                # A short's cost is minus its proceeds (before fees: enough to pro-rate).
                cost = (cost if held < 0 else 0) - added * fill['price']
            elif held > 0:
                cost = cost * (held - quantity) // held
            positions[symbol] = (held - quantity, cost)
        else:
            positions[symbol] = (held + quantity, cost)
