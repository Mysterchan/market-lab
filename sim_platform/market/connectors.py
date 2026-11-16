"""The interface for connecting a data vendor without bypassing look-ahead protection.

A connector reads from a vendor (Bloomberg, Refinitiv, FactSet, a database) and yields
records that carry their own publication time. ``export_connector`` writes them in the
formats the platform already loads, so every experiment reads vendor data through the same
point-in-time stores, tools and disguise as files: a connector never hands data to an
Agent directly. Market Lab ships no paid-data connector; write one with this Protocol.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Protocol, Union
import os

PathInput = Union[str, 'os.PathLike[str]']


class DataConnector(Protocol):
    """Yields point-in-time records for one symbol; each method may yield nothing."""

    def fundamentals(self, symbol: str) -> Iterable[Dict[str, Any]]:
        """Rows with symbol, metric, period_end, value, available_date, optional unit/form.

        ``available_date`` must be when the figure became public, never the period end.
        """
        ...

    def news(self, symbol: str) -> Iterable[Dict[str, Any]]:
        """Rows with published_at (ISO timestamp), headline and optional id/period."""
        ...

    def documents(self, symbol: str) -> Iterable[Dict[str, Any]]:
        """Rows with published_at, title, text and optional id/form."""
        ...


def export_connector(
    connector: DataConnector,
    symbol: str,
    folder: PathInput,
    session_start: str,
    *,
    delay_seconds: int = 0,
    provenance: Optional[str] = None,
) -> Dict[str, Optional[str]]:
    """Write a connector's records as fundamentals CSV, news JSON and documents JSON.

    Returns the paths written (None for a kind with no records); pass them as
    ``fundamentals_path``, the experiment's news file and ``documents_path``.
    """
    from .news_import import convert_documents, convert_news

    root = Path(folder)
    root.mkdir(parents=True, exist_ok=True)
    written: Dict[str, Optional[str]] = dict(fundamentals=None, news=None, documents=None)
    rows = list(connector.fundamentals(symbol))
    if rows:
        fields = ['symbol', 'metric', 'period_end', 'value', 'available_date', 'unit', 'form']
        path = root / f'{symbol}-fundamentals.csv'
        with path.open('w', newline='', encoding='utf-8') as stream:
            writer = csv.DictWriter(stream, fieldnames=fields, extrasaction='ignore')
            writer.writeheader()
            writer.writerows(rows)
        written['fundamentals'] = str(path)
    for kind, convert, options in (
        ('news', convert_news, dict(provenance=provenance or type(connector).__name__)),
        ('documents', convert_documents, {}),
    ):
        items = list(getattr(connector, kind)(symbol))
        if not items:
            continue
        staging = root / f'{symbol}-{kind}-source.jsonl'
        staging.write_text(
            ''.join(json.dumps(item, ensure_ascii=False) + '\n' for item in items),
            encoding='utf-8',
        )
        target = root / f'{symbol}-{kind}.json'
        convert(
            staging,
            target,
            session_start,
            id_column='id' if all('id' in i for i in items) else None,
            delay_seconds=delay_seconds,
            **options,
        )
        staging.unlink()
        written[kind] = str(target)
    return written
