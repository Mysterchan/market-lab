"""Conservative aggregate-depth budget; simulated orders do not alter historical Book."""

from __future__ import annotations

from typing import Collection, Dict, Iterable, List, Sequence, Tuple

from .models import BudgetLevel, Level


def refresh(previous: Iterable[Sequence[int]], levels: Sequence[Level]) -> List[BudgetLevel]:
    """Unchanged displayed quantity does not replenish consumed liquidity.

    Positive size changes add supply; negative changes remove it conservatively.
    Leaving retained depth removes the level's budget; returning grants no new
    supply unless displayed size exceeds its last observed size. This avoids
    interpreting depth truncation as new liquidity.

    Args:
        previous: The current budget as [price, displayed, available] levels.
        levels: The book side now displayed, as (price, displayed) levels.

    Returns:
        A new budget sorted by price; ``previous`` is not modified.
    """
    state: Dict[int, Tuple[int, int]] = {
        int(price): (displayed, available) for price, displayed, available in previous
    }
    current = dict(levels)
    for price, (displayed, _) in list(state.items()):
        if price not in current:
            state[price] = (displayed, 0)
    for price, displayed in levels:
        if price in state:
            old_displayed, available = state[price]
            # Only the increase in displayed size is new supply.
            replenished = max(0, available + displayed - old_displayed)
            state[price] = (displayed, min(displayed, replenished))
        else:
            state[price] = (displayed, displayed)
    return [
        [price, displayed, available] for price, (displayed, available) in sorted(state.items())
    ]


def consume(
    budget: List[BudgetLevel],
    side: str,
    limit: int,
    quantity: int,
    visible_prices: Collection[int],
) -> List[Tuple[int, int]]:
    """Mutate only a proposed transaction's budget; best price first.

    A buy takes the lowest asks up to ``limit``; a sell takes the highest bids down to
    ``limit``. Levels no longer displayed in the current book are skipped.

    Returns:
        (price, shares) for each level taken, in the order they were taken.
    """
    fills = []
    levels = sorted(budget, key=lambda level: level[0], reverse=side == 'sell')
    for level in levels:
        price, _, available = level
        if price not in visible_prices or not available:
            continue
        if (side == 'buy' and price > limit) or (side == 'sell' and price < limit):
            break
        amount = min(quantity, available)
        if amount:
            fills.append((price, amount))
            level[2] -= amount
            quantity -= amount
        if quantity == 0:
            break
    return fills
