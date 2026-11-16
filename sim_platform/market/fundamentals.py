"""Point-in-time company fundamentals: each figure is visible only once it was published.

A fundamental fact (revenue for a quarter, shares outstanding at a date) has two times:
the period it describes and the moment it became public. Backtests that use the period
end, or that read only the latest restated value, see the future. This store keeps every
published version and answers ``as_of(cutoff)`` with what an investor could have known
at that experiment time: for each period, the latest version published by then.

Times are experiment session nanoseconds, the same timeline as market events and news
(``available_time_ns``). ``session_time_ns`` converts a calendar date with the session's
first date. Two loaders are provided:

- ``load_fundamentals_csv``: columns symbol, metric, period_end, value, available_date,
  with optional unit and form.
- ``load_edgar_company_facts``: an SEC EDGAR "companyfacts" JSON file you downloaded,
  using each filing's ``filed`` date as the publication date.

Figures from a filing are treated as public from the start of the day after it was
filed, because filings arrive throughout the day and often after the close.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
import datetime
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union
import os

from sim_platform.domain.models import ContractError

NS_PER_DAY = 86_400 * 1_000_000_000
PathInput = Union[str, 'os.PathLike[str]']
# Limits that keep one Agent read bounded.
MAX_PERIODS = 20
MAX_METRICS = 50


@dataclass(frozen=True)
class FundamentalFact:
    """One published value of one metric for one period.

    ``period_end`` is the ISO date the figure describes; ``available_time_ns`` is when it
    became public on the experiment timeline. A restatement is another fact with the
    same symbol, metric and period and a later ``available_time_ns``.
    """

    symbol: str
    metric: str
    period_end: str
    value: float
    available_time_ns: int
    unit: str = ''
    form: str = ''

    def __post_init__(self) -> None:
        for name in ('symbol', 'metric'):
            text = getattr(self, name)
            if not isinstance(text, str) or not text.strip() or len(text) > 128:
                raise ContractError(f'fundamental {name} must be nonempty text')
        _date(self.period_end, 'period_end')
        if isinstance(self.value, bool) or not isinstance(self.value, (int, float)):
            raise ContractError('fundamental value must be a number')
        if self.value != self.value or self.value in (float('inf'), float('-inf')):
            raise ContractError('fundamental value must be finite')
        if type(self.available_time_ns) is not int or self.available_time_ns < 0:
            raise ContractError('available_time_ns must be a nonnegative integer')


def _date(text: Any, name: str) -> datetime.date:
    try:
        return datetime.date.fromisoformat(str(text)[:10])
    except ValueError as exc:
        raise ContractError(f'{name} must be an ISO date (YYYY-MM-DD)') from exc


def session_time_ns(date: str, session_start: str, *, next_day: bool = True) -> int:
    """Experiment time of a calendar date for a session whose first day is ``session_start``.

    With ``next_day`` the time is the start of the following day, the conservative
    moment a same-day publication is surely available. Dates before the session map
    to 0: already public when the experiment starts.
    """
    days = (_date(date, 'date') - _date(session_start, 'session_start')).days
    return max(0, (days + (1 if next_day else 0)) * NS_PER_DAY)


class FundamentalStore:
    """Every published version of every fact, queried by experiment time."""

    def __init__(self, facts: Iterable[FundamentalFact]) -> None:
        self._facts: Dict[Tuple[str, str], List[FundamentalFact]] = {}
        for fact in facts:
            self._facts.setdefault((fact.symbol, fact.metric), []).append(fact)
        for versions in self._facts.values():
            versions.sort(key=lambda f: (f.period_end, f.available_time_ns))

    def __len__(self) -> int:
        return sum(len(v) for v in self._facts.values())

    def symbols(self) -> List[str]:
        return sorted({symbol for symbol, _ in self._facts})

    def metrics(self, symbol: str, cutoff_ns: Optional[int] = None) -> List[str]:
        """Metrics with at least one value for ``symbol`` visible at ``cutoff_ns``."""
        return sorted(
            metric
            for (owner, metric), versions in self._facts.items()
            if owner == symbol
            and (cutoff_ns is None or any(f.available_time_ns <= cutoff_ns for f in versions))
        )

    def as_of(
        self,
        cutoff_ns: int,
        symbol: str,
        metrics: Optional[Sequence[str]] = None,
        periods: int = 4,
    ) -> Dict[str, List[Dict[str, Any]]]:
        """The latest ``periods`` values per metric that were public at ``cutoff_ns``.

        For each period the most recent version published by the cutoff is returned, so a
        later restatement stays hidden until its own publication time. Newest period first.

        Raises:
            ContractError: If ``periods`` or the number of metrics is out of bounds.
        """
        if type(periods) is not int or not 1 <= periods <= MAX_PERIODS:
            raise ContractError(f'periods must be 1..{MAX_PERIODS}')
        chosen = list(metrics) if metrics is not None else self.metrics(symbol, cutoff_ns)
        if len(chosen) > MAX_METRICS:
            raise ContractError(f'at most {MAX_METRICS} metrics per read')
        result: Dict[str, List[Dict[str, Any]]] = {}
        for metric in chosen:
            latest: Dict[str, FundamentalFact] = {}
            for fact in self._facts.get((symbol, metric), ()):
                if fact.available_time_ns <= cutoff_ns:
                    latest[fact.period_end] = fact  # sorted: later versions replace earlier
            ordered = sorted(latest.values(), key=lambda f: f.period_end, reverse=True)
            result[metric] = [
                dict(
                    period_end=f.period_end,
                    value=f.value,
                    unit=f.unit,
                    form=f.form,
                    available_time_ns=f.available_time_ns,
                )
                for f in ordered[:periods]
            ]
        return result


def load_fundamentals_csv(path: PathInput, session_start: str) -> FundamentalStore:
    """Read facts from a CSV with symbol, metric, period_end, value, available_date.

    ``available_date`` is the date the figure became public (a filing or press-release
    date, never the period end); optional ``unit`` and ``form`` columns are kept.

    Raises:
        ContractError: If a column is missing or a row is invalid (names the row).
    """
    facts = []
    with Path(path).open(newline='', encoding='utf-8') as stream:
        reader = csv.DictReader(stream)
        needed = {'symbol', 'metric', 'period_end', 'value', 'available_date'}
        if not needed <= set(reader.fieldnames or ()):
            raise ContractError('fundamentals CSV needs columns: ' + ', '.join(sorted(needed)))
        for number, row in enumerate(reader, start=2):
            try:
                facts.append(
                    FundamentalFact(
                        symbol=row['symbol'].strip(),
                        metric=row['metric'].strip(),
                        period_end=row['period_end'].strip(),
                        value=float(row['value']),
                        available_time_ns=session_time_ns(row['available_date'], session_start),
                        unit=(row.get('unit') or '').strip(),
                        form=(row.get('form') or '').strip(),
                    )
                )
            except (ContractError, ValueError) as exc:
                raise ContractError(f'fundamentals CSV row {number}: {exc}') from exc
    return FundamentalStore(facts)


def load_edgar_company_facts(
    path: PathInput,
    symbol: str,
    session_start: str,
    *,
    taxonomies: Sequence[str] = ('us-gaap', 'dei'),
) -> FundamentalStore:
    """Read an SEC EDGAR companyfacts JSON file into point-in-time facts for ``symbol``.

    Every reported value becomes a fact named ``<taxonomy>:<concept>`` (for example
    ``us-gaap:Revenues``), public from the day after its ``filed`` date. Values the
    company later restated in another filing appear as separate versions.

    Raises:
        ContractError: If the file is not a companyfacts document.
    """
    try:
        document = json.loads(Path(path).read_text(encoding='utf-8'))
        groups = document['facts']
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ContractError('not an EDGAR companyfacts JSON file') from exc
    facts = []
    for taxonomy in taxonomies:
        for concept, body in (groups.get(taxonomy) or {}).items():
            for unit, rows in (body.get('units') or {}).items():
                for row in rows:
                    if not {'end', 'val', 'filed'} <= set(row):
                        continue
                    facts.append(
                        FundamentalFact(
                            symbol=symbol,
                            metric=f'{taxonomy}:{concept}',
                            period_end=row['end'],
                            value=float(row['val']),
                            available_time_ns=session_time_ns(row['filed'], session_start),
                            unit=unit,
                            form=str(row.get('form') or ''),
                        )
                    )
    return FundamentalStore(facts)


def load_alfred_csv(path: PathInput, symbol: str, session_start: str) -> FundamentalStore:
    """Read an ALFRED all-vintages CSV of a macro series into point-in-time facts.

    ALFRED (the St. Louis Fed's archive) exports one column per release, named
    ``<SERIES>_<YYYYMMDD>`` after the vintage date, beside the observation date. Each
    value becomes a fact ``macro:<SERIES>`` for that observation, public from its vintage
    date, so revised figures replace earlier ones only once they were released. Facts
    are filed under ``symbol`` so the run's fundamentals.get can read them.

    Raises:
        ContractError: If the header has no vintage columns.
    """
    facts = []
    with Path(path).open(newline='', encoding='utf-8-sig') as stream:
        reader = csv.reader(stream)
        header = next(reader, None) or []
        vintages = []
        for index, name in enumerate(header[1:], 1):
            series, _, stamp = name.rpartition('_')
            if series and len(stamp) == 8 and stamp.isdigit():
                vintages.append((index, series, f'{stamp[:4]}-{stamp[4:6]}-{stamp[6:]}'))
        if not vintages:
            raise ContractError('not an ALFRED vintage CSV: no <SERIES>_<YYYYMMDD> columns')
        for cells in reader:
            if not cells or not cells[0].strip():
                continue
            for index, series, vintage in vintages:
                text = cells[index].strip() if index < len(cells) else ''
                if text in ('', '.', '#N/A'):
                    continue
                facts.append(
                    FundamentalFact(
                        symbol=symbol,
                        metric=f'macro:{series}',
                        period_end=cells[0].strip()[:10],
                        value=float(text),
                        available_time_ns=session_time_ns(vintage, session_start),
                        form='ALFRED',
                    )
                )
    return FundamentalStore(facts)


def load_fundamentals_path(path: PathInput, symbol: str, session_start: str) -> FundamentalStore:
    """Load one file or every file of a folder into one store, choosing each loader.

    ``.json`` files are EDGAR companyfacts for ``symbol``; a CSV with an
    ``available_date`` column is a fundamentals CSV (company figures, analyst estimates
    or anything else with a publication date); any other CSV is read as an ALFRED
    vintage export. Files in a folder are read in name order.
    """
    source = Path(path)
    files = sorted(p for p in source.iterdir() if p.is_file()) if source.is_dir() else [source]
    facts: List[FundamentalFact] = []
    for file in files:
        suffix = file.suffix.lower()
        if suffix == '.json':
            store = load_edgar_company_facts(file, symbol, session_start)
        elif suffix == '.csv':
            with file.open(newline='', encoding='utf-8-sig') as stream:
                header = next(csv.reader(stream), [])
            store = (
                load_fundamentals_csv(file, session_start)
                if 'available_date' in header
                else load_alfred_csv(file, symbol, session_start)
            )
        else:
            continue
        facts.extend(f for versions in store._facts.values() for f in versions)
    return FundamentalStore(facts)
