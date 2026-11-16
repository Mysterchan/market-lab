"""Disguise a price-bar file so a model cannot recognise the market from memory.

A disguised copy shifts every date by a whole number of weeks, so weekdays are kept, and
multiplies every price by one factor, rounded to the cent. Volumes and times of day are
kept. Every method then trades the same disguised market, so comparisons stay fair.
See docs/user-guide/evaluation.md#disguise-the-market.
"""

from __future__ import annotations

import csv
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path
from random import Random
from typing import Any, Dict, Optional

from sim_platform.domain.models import ContractError

from .bars import PRICE_SCALE, TICK, _rows

# First date of a disguised file unless chosen: a Monday no model has seen.
DEFAULT_START_DATE = '2100-01-04'
# A price factor drawn for the caller lies in this range.
SCALE_RANGE = (0.5, 2.0)
DEFAULT_SEED = 2026
_CENT = Decimal(PRICE_SCALE // TICK)


def _cents(price: int, scale: Decimal) -> Decimal:
    """A price in integer ten-thousandths, scaled and rounded to the cent, in dollars."""
    cents = (Decimal(price) / TICK * scale).quantize(Decimal(1), ROUND_HALF_EVEN)
    if cents <= 0:
        raise ContractError('price scale makes a price round to zero')
    return (cents / _CENT).quantize(Decimal('0.01'))


def disguise_price_bars(
    source: Path,
    output: Path,
    *,
    start_date: str = DEFAULT_START_DATE,
    price_scale: Optional[float] = None,
    seed: int = DEFAULT_SEED,
) -> Dict[str, Any]:
    """Write a disguised copy of ``source`` to ``output`` and return the mapping.

    The first bar's date moves to ``start_date`` or up to six days after it, keeping its
    weekday. ``price_scale`` multiplies every price; when None a factor is drawn from
    [0.5, 2] with ``seed``. Columns other than the time, prices and volume are dropped,
    since an adjusted close or a ticker column would give the market away.

    The mapping holds 'source', 'output', 'first_date', 'disguised_first_date',
    'shift_days' and 'price_scale'. It is for the researcher; keep it away from Agents.
    """
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise ContractError('seed must be an integer')
    try:
        target = date.fromisoformat(start_date)
    except (TypeError, ValueError):
        raise ContractError('start_date must be a YYYY-MM-DD date') from None
    if price_scale is None:
        price_scale = round(Random(seed).uniform(*SCALE_RANGE), 4)
    if (
        not isinstance(price_scale, (int, float))
        or isinstance(price_scale, bool)
        or not 0.01 <= price_scale <= 100
    ):
        raise ContractError('price_scale must be a number from 0.01 to 100')
    source, output = Path(source), Path(output)
    if source.resolve() == output.resolve():
        raise ContractError('write the disguised copy to a different file')
    rows = sorted(_rows(source), key=lambda row: row[0])
    first = rows[0][0].date()
    weeks = -(-(target - first).days // 7)
    shift = timedelta(days=7 * weeks)
    daily = all(row[0].time() == datetime.min.time() for row in rows)
    scale = Decimal(str(price_scale))
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('w', newline='', encoding='utf-8') as stream:
        writer = csv.writer(stream)
        writer.writerow(
            ['datetime' if not daily else 'date', 'open', 'high', 'low', 'close', 'volume']
        )
        for moment, opened, high, low, closed, volume in rows:
            moved = moment + shift
            writer.writerow(
                [
                    moved.date().isoformat() if daily else moved.isoformat(sep=' '),
                    *(str(_cents(price, scale)) for price in (opened, high, low, closed)),
                    volume,
                ]
            )
    return dict(
        source=str(source),
        output=str(output),
        first_date=first.isoformat(),
        disguised_first_date=(first + shift).isoformat(),
        shift_days=shift.days,
        price_scale=price_scale,
    )
