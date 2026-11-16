"""Point-in-time index membership, to choose test symbols without survivorship bias.

Picking today's index members and testing them over past years keeps only the companies
that survived and grew, which inflates every strategy's results. A membership file lists
when each symbol joined and left the universe (an index, a liquidity screen), so the
symbols for a test window are the members on its first day, including those later
delisted or dropped.

CSV columns: ``symbol``, ``start_date`` and optional ``end_date`` (the first day the
symbol was no longer a member; empty while it still is) and ``reason`` (for example
``delisted`` or ``acquired``).
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Union
import os

from sim_platform.domain.models import ContractError

PathInput = Union[str, 'os.PathLike[str]']


@dataclass(frozen=True)
class Membership:
    symbol: str
    start: date
    end: Optional[date]
    reason: str = ''

    def active(self, day: date) -> bool:
        return self.start <= day and (self.end is None or day < self.end)


def load_membership(path: PathInput) -> List[Membership]:
    """Read a membership CSV; a symbol may have several spells.

    Raises:
        ContractError: If a column is missing, a date is invalid or a spell ends before it
            starts (names the row).
    """
    spells = []
    with Path(path).open(newline='', encoding='utf-8-sig') as stream:
        reader = csv.DictReader(stream)
        if not {'symbol', 'start_date'} <= set(reader.fieldnames or ()):
            raise ContractError('membership CSV needs symbol and start_date columns')
        for number, row in enumerate(reader, 2):
            try:
                start = date.fromisoformat(row['start_date'].strip()[:10])
                text = (row.get('end_date') or '').strip()
                end = date.fromisoformat(text[:10]) if text else None
            except ValueError as exc:
                raise ContractError(f'membership row {number}: invalid date') from exc
            if end is not None and end <= start:
                raise ContractError(f'membership row {number}: end_date is not after start')
            spells.append(
                Membership(row['symbol'].strip(), start, end, (row.get('reason') or '').strip())
            )
    return spells


def members_on(spells: Iterable[Membership], day: str) -> List[str]:
    """Symbols that were members on ``day`` (YYYY-MM-DD), including later leavers."""
    when = date.fromisoformat(day[:10])
    return sorted({s.symbol for s in spells if s.active(when)})


def survivorship_check(
    spells: Iterable[Membership], symbols: Iterable[str], start: str, end: str
) -> Dict[str, Any]:
    """Compare chosen ``symbols`` with the universe over a test window [start, end].

    Returns ``not_members`` (chosen symbols that were not members on the first day: they
    were picked with later knowledge), ``left_during`` (members on the first day that
    left before ``end``, with their reason) and ``missing_leavers`` (those leavers that
    are absent from ``symbols``: their omission is survivorship bias).
    """
    spells = list(spells)
    first, last = date.fromisoformat(start[:10]), date.fromisoformat(end[:10])
    chosen = set(symbols)
    members = set(members_on(spells, start))
    leavers = {
        s.symbol: s.reason or 'left'
        for s in spells
        if s.active(first) and s.end is not None and s.end <= last
    }
    return dict(
        members=len(members),
        not_members=sorted(chosen - members),
        left_during=dict(sorted(leavers.items())),
        missing_leavers=sorted(set(leavers) - chosen),
        biased=bool(chosen - members or set(leavers) - chosen),
    )
