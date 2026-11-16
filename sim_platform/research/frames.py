"""Saved run reports as flat tables for pandas, polars, Jupyter, CSV or Parquet.

``report_tables`` needs no extra package: it returns lists of flat dicts. ``report_frames``
returns pandas DataFrames when pandas is installed, and ``export_report_tables`` writes the
tables as CSV or (with pyarrow) Parquet files. Money stays in integer units of USD 1/10,000
(``MONEY_SCALE``), the platform's exact representation; divide by it for dollars.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Dict, List, Union
import os

from sim_platform.domain.models import ContractError

from .metrics import performance
from .views import session_view

PathInput = Union[str, 'os.PathLike[str]']
MONEY_SCALE = 10_000
Table = List[Dict[str, Any]]


def report_tables(report: Dict[str, Any]) -> Dict[str, Table]:
    """The run's ``fills``, ``equity`` curve and ``figures`` (one row) as flat rows.

    ``report`` is a saved run report (research.reports.run_report or a batch result).
    """
    view = session_view(dict(mode='saved', report=report))
    result = performance(view)
    run_id = (report.get('run') or {}).get('id')
    fills = [dict(run_id=run_id, **row) for row in result['fills']]
    equity = [
        dict(run_id=run_id, time_ns=time_ns, equity=value)
        for time_ns, value in (result.get('series') or [])
    ]
    figures = {
        key: value
        for key, value in result.items()
        if key not in ('fills', 'series') and not isinstance(value, (list, dict))
    }
    return dict(fills=fills, equity=equity, figures=[dict(run_id=run_id, **figures)])


def report_frames(report: Dict[str, Any]) -> Dict[str, Any]:
    """report_tables() as pandas DataFrames.

    Raises:
        ContractError: If pandas is not installed.
    """
    try:
        import pandas
    except ImportError:
        raise ContractError('report_frames needs pandas: pip install pandas') from None
    return {name: pandas.DataFrame(rows) for name, rows in report_tables(report).items()}


def export_report_tables(
    report: Dict[str, Any], folder: PathInput, *, file_format: str = 'csv'
) -> Dict[str, str]:
    """Write each table to ``<folder>/<table>.csv`` or ``.parquet``; return the paths.

    Raises:
        ContractError: For an unknown format, or Parquet without pyarrow.
    """
    if file_format not in ('csv', 'parquet'):
        raise ContractError("file_format must be 'csv' or 'parquet'")
    root = Path(folder)
    root.mkdir(parents=True, exist_ok=True)
    written = {}
    for name, rows in report_tables(report).items():
        path = root / f'{name}.{file_format}'
        if file_format == 'parquet':
            from sim_platform.market.parquet import write_parquet

            write_parquet(rows, path)
        else:
            fields = sorted({key for row in rows for key in row}) or ['run_id']
            with path.open('w', newline='', encoding='utf-8') as stream:
                writer = csv.DictWriter(stream, fieldnames=fields)
                writer.writeheader()
                writer.writerows(rows)
        written[name] = str(path)
    return written
