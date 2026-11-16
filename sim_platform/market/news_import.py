"""Convert a vendor's news or filings export into Market Lab's timed records.

Most vendors export a table with a publication timestamp and a headline (and, for filings,
a body). ``convert_news`` turns a CSV or JSON Lines export into the news JSON file the
``news`` option reads; ``convert_documents`` makes the documents file read by the
``documents.search`` Agent tool. Both place each record on the experiment timeline at its
publication time, so nothing is visible before it was published.

Timestamps are read as written (a time-zone suffix is dropped, not applied), the same
convention as price-bar files: export both in the same zone. ``delay_seconds`` adds a
processing delay, a conservative allowance for how long a feed takes to arrive.
"""

from __future__ import annotations

import csv
from datetime import datetime
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Union
import os

from sim_platform.domain.models import ContractError

PathInput = Union[str, 'os.PathLike[str]']
NS_PER_SECOND = 1_000_000_000
MAX_DOCUMENT_CHARACTERS = 2_000_000


def _rows(source: Path) -> Iterator[Dict[str, Any]]:
    if source.suffix.lower() in ('.jsonl', '.ndjson'):
        with source.open(encoding='utf-8') as stream:
            for number, line in enumerate(stream, 1):
                if line.strip():
                    try:
                        value = json.loads(line)
                    except ValueError as exc:
                        raise ContractError(f'line {number} is not JSON') from exc
                    if not isinstance(value, dict):
                        raise ContractError(f'line {number} is not a JSON object')
                    yield value
        return
    with source.open(newline='', encoding='utf-8-sig') as stream:
        yield from csv.DictReader(stream)


def _moment(text: Any, row: int) -> datetime:
    value = str(text).strip().replace('Z', '+00:00')
    try:
        return datetime.fromisoformat(value).replace(tzinfo=None)
    except ValueError:
        raise ContractError(f'row {row}: unrecognised timestamp {text!r}') from None


def _session_time(published: datetime, session_start: str, delay_seconds: int) -> int:
    origin = datetime.fromisoformat(session_start[:10])
    offset = (published - origin).total_seconds() + delay_seconds
    return max(0, int(round(offset * NS_PER_SECOND)))


def _identity(*parts: Any) -> str:
    return hashlib.sha256('|'.join(str(p) for p in parts).encode()).hexdigest()[:16]


def convert_news(
    source: PathInput,
    output: PathInput,
    session_start: str,
    *,
    time_column: str = 'published_at',
    headline_column: str = 'headline',
    id_column: Optional[str] = None,
    period_column: Optional[str] = None,
    delay_seconds: int = 0,
    provenance: str = 'vendor_export',
) -> Dict[str, Any]:
    """Write a news JSON file from a CSV or JSON Lines export.

    Args:
        source: The export; ``.jsonl``/``.ndjson`` are read as JSON Lines, others as CSV.
        output: The news JSON file to write.
        session_start: First date of the recording the news goes with (YYYY-MM-DD).
        time_column: Column holding each item's publication timestamp.
        headline_column: Column holding the headline.
        id_column: Column with a stable item ID; None derives one from time and headline.
        period_column: Optional column naming the period the item is about.
        delay_seconds: Extra seconds before an item becomes visible.
        provenance: Recorded with every item, for example the vendor's name.

    Returns:
        ``output``, the number of ``records`` and the first and last publication times.

    Raises:
        ContractError: If a column is missing or a timestamp cannot be read.
    """
    records: List[Dict[str, Any]] = []
    for number, row in enumerate(_rows(Path(source)), 2):
        if time_column not in row or headline_column not in row:
            raise ContractError(f'row {number}: needs {time_column} and {headline_column}')
        published = _moment(row[time_column], number)
        headline = str(row[headline_column]).strip()
        if not headline:
            continue
        records.append(
            dict(
                record_id=str(row[id_column]) if id_column else _identity(published, headline),
                available_time_ns=_session_time(published, session_start, delay_seconds),
                period=str(row.get(period_column) or '') if period_column else '',
                headline=headline[:1000],
                provenance=provenance,
            )
        )
    records.sort(key=lambda r: (r['available_time_ns'], r['record_id']))
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding='utf-8')
    return dict(
        output=str(target),
        records=len(records),
        first_ns=records[0]['available_time_ns'] if records else None,
        last_ns=records[-1]['available_time_ns'] if records else None,
    )


def convert_documents(
    source: PathInput,
    output: PathInput,
    session_start: str,
    *,
    time_column: str = 'published_at',
    title_column: str = 'title',
    text_column: str = 'text',
    id_column: Optional[str] = None,
    form_column: Optional[str] = None,
    delay_seconds: int = 0,
) -> Dict[str, Any]:
    """Write a documents JSON file (filings, transcripts, reports) for documents.search.

    Arguments mirror convert_news(); ``text_column`` holds each document's full text
    (at most 2,000,000 characters) and ``form_column`` an optional type such as 10-K.
    """
    documents: List[Dict[str, Any]] = []
    for number, row in enumerate(_rows(Path(source)), 2):
        for column in (time_column, title_column, text_column):
            if column not in row:
                raise ContractError(f'row {number}: needs column {column}')
        published = _moment(row[time_column], number)
        text = str(row[text_column])
        if len(text) > MAX_DOCUMENT_CHARACTERS:
            raise ContractError(f'row {number}: document text is too long')
        documents.append(
            dict(
                document_id=(
                    str(row[id_column]) if id_column else _identity(published, row[title_column])
                ),
                available_time_ns=_session_time(published, session_start, delay_seconds),
                title=str(row[title_column]).strip()[:500],
                form=str(row.get(form_column) or '') if form_column else '',
                text=text,
            )
        )
    documents.sort(key=lambda d: (d['available_time_ns'], d['document_id']))
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(documents, ensure_ascii=False), encoding='utf-8')
    return dict(output=str(target), documents=len(documents))
