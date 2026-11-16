"""Read price bars from Parquet and write results to Parquet, with the optional pyarrow.

Install with ``pip install "market-lab[parquet]"``. ``convert_parquet_bars`` turns a
Parquet file of bars into the price-bar CSV every experiment reads, so the CSV checks and
synthetic book apply unchanged. Column names are matched as in CSV files (case-insensitive:
date or datetime/timestamp, open, high, low, close, volume).
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Dict, Union
import os

from sim_platform.domain.models import ContractError

PathInput = Union[str, 'os.PathLike[str]']


def _pyarrow() -> Any:
    try:
        import pyarrow.parquet as parquet
    except ImportError:
        raise ContractError('Parquet needs pyarrow: pip install "market-lab[parquet]"') from None
    return parquet


def convert_parquet_bars(source: PathInput, output: PathInput) -> Dict[str, Any]:
    """Write a price-bar CSV from a Parquet file of bars; return its path and row count.

    Raises:
        ContractError: If pyarrow is missing or the needed columns are absent.
    """
    table = _pyarrow().read_table(str(source))
    names = {name.lower(): name for name in table.column_names}
    stamp = next((names[n] for n in ('date', 'datetime', 'timestamp', 'time') if n in names), None)
    needed = ['open', 'high', 'low', 'close']
    volume = names.get('volume') or names.get('vol')
    if stamp is None or volume is None or any(n not in names for n in needed):
        raise ContractError('Parquet bars need a date, open, high, low, close and volume column')
    columns = [stamp] + [names[n] for n in needed] + [volume]
    rows = zip(*(table.column(c).to_pylist() for c in columns))
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with target.open('w', newline='', encoding='utf-8') as stream:
        writer = csv.writer(stream)
        writer.writerow(['Date', 'Open', 'High', 'Low', 'Close', 'Volume'])
        for when, *values in rows:
            text = when.isoformat() if hasattr(when, 'isoformat') else str(when)
            writer.writerow([text] + values)
            count += 1
    return dict(output=str(target), rows=count)


def write_parquet(rows: Any, path: PathInput) -> str:
    """Write a list of flat dicts (for example report_rows() output) as one Parquet file."""
    parquet = _pyarrow()
    import pyarrow

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    parquet.write_table(pyarrow.Table.from_pylist(list(rows)), str(target))
    return str(target)
