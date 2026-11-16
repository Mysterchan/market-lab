"""Disguise the text and fundamentals that accompany a disguised price recording.

A language model can recognise a company from its name in a headline, or a quarter from
its date, even when prices are disguised. These helpers rewrite news and fundamentals with
the same date shift as the price file and replace the names you list with an alias, so
every input of a disguised experiment hides the same market.

News ``available_time_ns`` values are experiment times relative to the session start, so
they stay correct unchanged. Calendar dates and years in text are moved by the shift.
"""

from __future__ import annotations

import csv
from datetime import date, timedelta
import json
from pathlib import Path
import re
from typing import Any, Dict, List, Sequence

from sim_platform.domain.models import ContractError

# An ISO date, or a bare year (also in 2020Q1 or 2020H1). One pass, so a moved date's
# year is never moved again.
_DATE_OR_YEAR = re.compile(
    r'\b(\d{4})-(\d{2})-(\d{2})\b|\b(19\d{2}|20\d{2})(?=\b|Q[1-4]\b|H[12]\b)'
)


def shift_text(text: str, shift_days: int, aliases: Dict[str, str]) -> str:
    """Replace listed names (whole words, any case) and move dates and years in ``text``.

    ISO dates move by ``shift_days``; a bare year (also in 2020Q1 or 2020H1) moves by the
    whole number of years nearest to the shift. Longer names are replaced first, so
    'Apple Inc' wins over 'Apple'.
    """
    for name in sorted(aliases, key=len, reverse=True):
        text = re.sub(r'\b' + re.escape(name) + r'\b', aliases[name], text, flags=re.IGNORECASE)

    years = round(shift_days / 365.25)

    def move(match: Any) -> str:
        if match.group(4):
            return str(int(match.group(4)) + years)
        moved = date(*(int(match.group(i)) for i in (1, 2, 3))) + timedelta(days=shift_days)
        return moved.isoformat()

    return _DATE_OR_YEAR.sub(move, text)


def _aliases(names: Sequence[str], alias: str) -> Dict[str, str]:
    if not isinstance(names, (list, tuple)) or not all(
        isinstance(n, str) and n.strip() for n in names
    ):
        raise ContractError('names must be a list of company names or tickers')
    return {name.strip(): alias for name in names}


def disguise_news(
    source: Path, output: Path, shift_days: int, names: Sequence[str], alias: str
) -> int:
    """Write a disguised copy of a news JSON file; return the number of records."""
    aliases = _aliases(names, alias)
    try:
        records = json.loads(Path(source).read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise ContractError('news file must be readable JSON') from exc
    if not isinstance(records, list) or not all(isinstance(r, dict) for r in records):
        raise ContractError('news file must be a JSON list of records')
    disguised: List[Dict[str, Any]] = []
    for record in records:
        copy = dict(record)
        for key in ('headline', 'period'):
            if isinstance(copy.get(key), str):
                copy[key] = shift_text(copy[key], shift_days, aliases)
        disguised.append(copy)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(disguised, indent=2, ensure_ascii=False), encoding='utf-8')
    return len(disguised)


def disguise_fundamentals(
    source: Path,
    output: Path,
    shift_days: int,
    symbol: str,
    names: Sequence[str],
    alias: str,
) -> int:
    """Write a disguised copy of a fundamentals CSV (market.fundamentals format).

    Period ends and publication dates move by ``shift_days``; every row's symbol becomes
    ``symbol`` and listed names in metric or form text become the alias. Values are kept:
    they describe the business, not the price level. Returns the number of rows.
    """
    aliases = _aliases(names, alias)
    with Path(source).open(newline='', encoding='utf-8') as stream:
        reader = csv.DictReader(stream)
        fields = list(reader.fieldnames or ())
        if not {'symbol', 'period_end', 'available_date'} <= set(fields):
            raise ContractError('fundamentals CSV needs symbol, period_end and available_date')
        rows = list(reader)
    for row in rows:
        row['symbol'] = symbol
        for key in ('period_end', 'available_date'):
            moved = date.fromisoformat(row[key].strip()[:10]) + timedelta(days=shift_days)
            row[key] = moved.isoformat()
        for key in ('metric', 'form'):
            if row.get(key):
                row[key] = shift_text(row[key], 0, aliases)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)
