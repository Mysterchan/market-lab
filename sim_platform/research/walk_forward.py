"""Cut a price-bar recording into walk-forward windows of separate files.

Tune parameters, prompts or a model on each training window, then evaluate only on the
test window that follows it (``ResearchBatch.evaluate_suite`` over the test files). No
test date ever precedes the end of its training window, so a choice made in training
cannot use test data. Windows count trading dates present in the file.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union
import os

from sim_platform.domain.models import ContractError
from sim_platform.market.prepared.bars import _columns, _moment

from .overfitting import walk_forward_splits

PathInput = Union[str, 'os.PathLike[str]']


def _dated_rows(source: Path) -> tuple:
    with source.open(newline='', encoding='utf-8-sig') as stream:
        reader = csv.reader(stream)
        header = next(reader, None)
        if header is None:
            raise ContractError('bar file is empty')
        columns = _columns(header)
        rows = []
        for number, cells in enumerate(reader, 2):
            if not any(cell.strip() for cell in cells):
                continue
            stamp = (
                cells[columns['moment']]
                if 'moment' in columns
                else cells[columns['date']] + ' ' + cells[columns['time']]
            )
            rows.append((_moment(stamp, number).date().isoformat(), cells))
    return header, sorted(rows, key=lambda row: row[0])


def _write(path: Path, header: Sequence[str], rows: List[Any]) -> None:
    with path.open('w', newline='', encoding='utf-8') as stream:
        writer = csv.writer(stream)
        writer.writerow(header)
        writer.writerows(cells for _, cells in rows)


def split_recording(
    source: PathInput,
    output: PathInput,
    *,
    train_days: int,
    test_days: int,
    step_days: Optional[int] = None,
    gap_days: int = 0,
    anchored: bool = False,
) -> List[Dict[str, Any]]:
    """Write train and test CSV files for each walk-forward window of a bar recording.

    Args:
        source: A price-bar CSV recording (see docs/reference/price-bars.md).
        output: Folder for the window files; created when missing. Existing window files
            of the same names are replaced.
        train_days: Trading dates in each training window (the first one when anchored).
        test_days: Trading dates in each test window.
        step_days: How far each window moves; defaults to ``test_days``.
        gap_days: Trading dates skipped between training and test, for signals that
            look ahead (for example a label over the next five days).
        anchored: Keep every training window starting at the first date.

    Returns:
        One record per window: ``train`` and ``test`` file paths and their first and last
        dates (``train_dates``, ``test_dates``).

    Raises:
        ContractError: If the file cannot be read or holds too few dates for one window.
    """
    source = Path(source).expanduser().resolve()
    folder = Path(output).expanduser()
    header, rows = _dated_rows(source)
    dates = sorted({date for date, _ in rows})
    try:
        splits = walk_forward_splits(
            len(dates), train_days, test_days, step=step_days, anchored=anchored, gap=gap_days
        )
    except ValueError as exc:
        raise ContractError(str(exc)) from exc
    if not splits:
        raise ContractError(
            f'{len(dates)} trading dates are too few for one {train_days}+{gap_days}+'
            f'{test_days}-day window'
        )
    folder.mkdir(parents=True, exist_ok=True)
    windows = []
    for number, ((train_start, train_stop), (test_start, test_stop)) in enumerate(splits, 1):
        record: Dict[str, Any] = dict(window=number)
        for part, (start, stop) in (
            ('train', (train_start, train_stop)),
            ('test', (test_start, test_stop)),
        ):
            chosen = set(dates[start:stop])
            path = folder / f'{source.stem}-window-{number:02d}-{part}.csv'
            _write(path, header, [row for row in rows if row[0] in chosen])
            record[part] = str(path)
            record[part + '_dates'] = [dates[start], dates[stop - 1]]
        windows.append(record)
    return windows
