"""Experiment sweeps: run a grid of parameters or prompts and tabulate the results.

A sweep starts from one suite entry (a rule, model-file or Agent entry, see
``ResearchBatch.evaluate_suite``) and a grid that names entry fields by dotted path,
for example ``{'quantity': [1, 5], 'hold_ns': [...]}`` for a rule or
``{'model.temperature': [0, 0.7], 'settings.budget.max_tokens': [...]}`` for an Agent.
Every combination becomes its own entry; all of them run on every recording with the
same seeds, so the table compares like with like.

Choosing the best cell of a grid is itself a search, so every entry is evaluated with
``trials`` equal to the number of combinations: its deflated Sharpe ratio accounts for
the whole sweep, and the probability of backtest overfitting is reported across cells.
"""

from __future__ import annotations

from itertools import product
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from sim_platform.domain.json_values import copy_json
from sim_platform.domain.models import ContractError

# Most combinations one sweep may run.
MAX_COMBINATIONS = 200
# Combinations evaluated together in one suite (the evaluation's entry limit).
CHUNK = 20


def _assign(entry: Dict[str, Any], path: str, value: Any) -> None:
    keys = path.split('.')
    target = entry
    for key in keys[:-1]:
        child = target.get(key)
        if child is None:
            child = target[key] = {}
        if not isinstance(child, dict):
            raise ContractError(f'sweep field {path!r} does not name a setting')
        target = child
    target[keys[-1]] = copy_json(value)


def _label(value: Any) -> str:
    text = value if isinstance(value, str) else repr(value)
    return text if len(text) <= 40 else text[:37] + '...'


def expand_grid(
    base: Mapping[str, Any], grid: Mapping[str, Sequence[Any]]
) -> List[Tuple[Dict[str, Any], Dict[str, Any]]]:
    """Every combination of ``grid`` applied to ``base``: (parameters, suite entry) pairs.

    Entry names list the parameters (``quantity=5, hold_ns=60``); a grid field named
    ``name`` or ``repetitions`` is refused, since those describe the sweep itself.
    """
    if not isinstance(grid, Mapping) or not grid:
        raise ContractError('a sweep needs a grid of at least one field')
    fields = list(grid)
    for field in fields:
        if field in ('name', 'repetitions', 'policy') or not field:
            raise ContractError(f'{field!r} cannot be swept')
        values = grid[field]
        if not isinstance(values, (list, tuple)) or not values:
            raise ContractError(f'sweep field {field!r} needs a list of values')
    combinations = list(product(*(grid[field] for field in fields)))
    if len(combinations) > MAX_COMBINATIONS:
        raise ContractError(f'a sweep may run at most {MAX_COMBINATIONS} combinations')
    result = []
    for values in combinations:
        entry = copy_json(dict(base))
        parameters = dict(zip(fields, values))
        for field, value in parameters.items():
            _assign(entry, field, value)
        entry['name'] = ', '.join(f'{field}={_label(value)}' for field, value in parameters.items())
        result.append((copy_json(parameters), entry))
    if len({entry['name'] for _, entry in result}) != len(result):
        raise ContractError('sweep values must be distinct')
    return result


def sweep_table(
    combinations: Sequence[Tuple[Dict[str, Any], Dict[str, Any]]],
    suites: Sequence[Mapping[str, Any]],
    metric: str,
) -> List[Dict[str, Any]]:
    """One row per combination: its parameters and, per recording, the metric's figures."""
    by_name: Dict[str, Dict[str, Any]] = {}
    for suite in suites:
        for recording in suite.get('recordings') or []:
            for row in recording['evaluation']['entries']:
                statistics = (row.get('statistics') or {}).get(metric) or {}
                selection = row.get('selection') or {}
                by_name.setdefault(row['name'], {})[recording['path']] = dict(
                    mean=statistics.get('mean'),
                    n=statistics.get('n'),
                    interval=statistics.get('interval'),
                    sharpe=selection.get('sharpe_per_period'),
                    deflated_sharpe=selection.get('deflated_sharpe'),
                    run_ids=[run['run_id'] for run in row.get('runs') or []],
                )
    rows = []
    for parameters, entry in combinations:
        recordings = by_name.get(entry['name'], {})
        means = [r['mean'] for r in recordings.values() if r['mean'] is not None]
        rows.append(
            dict(
                name=entry['name'],
                parameters=parameters,
                recordings=recordings,
                mean=sum(means) / len(means) if means else None,
            )
        )
    return rows


def heatmap(
    rows: Sequence[Mapping[str, Any]], x: str, y: str, *, value: str = 'mean'
) -> Dict[str, Any]:
    """A matrix of ``value`` over two swept fields; other fields are averaged over.

    Returns ``{'x', 'y', 'x_values', 'y_values', 'matrix'}`` with ``matrix[i][j]`` for
    ``y_values[i]`` and ``x_values[j]`` (None where no combination has a value).
    """
    x_values: List[Any] = []
    y_values: List[Any] = []
    cells: Dict[Tuple[int, int], List[float]] = {}
    for row in rows:
        parameters = row['parameters']
        if x not in parameters or y not in parameters:
            raise ContractError('heat map axes must both be swept fields')
        for values, key in ((x_values, x), (y_values, y)):
            if parameters[key] not in values:
                values.append(parameters[key])
        if row.get(value) is not None:
            position = (y_values.index(parameters[y]), x_values.index(parameters[x]))
            cells.setdefault(position, []).append(float(row[value]))
    matrix = [
        [
            sum(cells[(i, j)]) / len(cells[(i, j)]) if (i, j) in cells else None
            for j in range(len(x_values))
        ]
        for i in range(len(y_values))
    ]
    return dict(x=x, y=y, value=value, x_values=x_values, y_values=y_values, matrix=matrix)


def _colour(fraction: float) -> str:
    """A diverging blue-white-red colour for a value scaled to 0..1."""
    fraction = min(1.0, max(0.0, fraction))
    if fraction < 0.5:
        level = fraction * 2
        return f'rgb({int(33 + level * 222)},{int(102 + level * 153)},{int(172 + level * 83)})'
    level = (fraction - 0.5) * 2
    return f'rgb({int(255 - level * 77)},{int(255 - level * 231)},{int(255 - level * 212)})'


def render_heatmap_svg(data: Mapping[str, Any], *, title: Optional[str] = None) -> str:
    """An SVG heat map of ``heatmap()`` output, symmetric around zero, values in cells."""
    cell, left, top = 72, 150, 56
    width = left + cell * len(data['x_values']) + 20
    height = top + cell * len(data['y_values']) + 60
    values = [v for row in data['matrix'] for v in row if v is not None]
    scale = max((abs(v) for v in values), default=1.0) or 1.0

    def escape(text: Any) -> str:
        return str(text).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        'font-family="sans-serif" font-size="12">',
        f'<text x="{left}" y="20" font-size="14">{escape(title or data["value"])}</text>',
    ]
    for j, value in enumerate(data['x_values']):
        parts.append(
            f'<text x="{left + j * cell + cell / 2}" y="{top - 8}" text-anchor="middle">'
            f'{escape(_label(value))}</text>'
        )
    for i, value in enumerate(data['y_values']):
        parts.append(
            f'<text x="{left - 8}" y="{top + i * cell + cell / 2 + 4}" text-anchor="end">'
            f'{escape(_label(value))}</text>'
        )
        for j, number in enumerate(data['matrix'][i]):
            fill = '#eeeeee' if number is None else _colour(0.5 + number / scale / 2)
            parts.append(
                f'<rect x="{left + j * cell}" y="{top + i * cell}" width="{cell}" '
                f'height="{cell}" fill="{fill}" stroke="#ffffff"/>'
            )
            label = '-' if number is None else f'{number:.4g}'
            parts.append(
                f'<text x="{left + j * cell + cell / 2}" y="{top + i * cell + cell / 2 + 4}" '
                f'text-anchor="middle">{label}</text>'
            )
    parts.append(
        f'<text x="{left}" y="{height - 30}">x: {escape(data["x"])}   y: {escape(data["y"])}'
        '   (blue below zero, red above)</text>'
    )
    parts.append('</svg>')
    return '\n'.join(parts)


def run_sweep(
    batch: Any,
    paths: List[Any],
    base: Mapping[str, Any],
    grid: Mapping[str, Sequence[Any]],
    *,
    metric: str = 'total_return',
    **arguments: Any,
) -> Dict[str, Any]:
    """Run every combination on every recording through ``batch.evaluate_suite``.

    Returns ``{'format', 'metric', 'grid', 'combinations', 'rows', 'best', 'suites'}``:
    ``rows`` from ``sweep_table``; ``best`` the combination with the best mean (by
    the metric's direction; None for a metric without one), whose deflated Sharpe
    ratio counts every combination as a trial. ``arguments`` go to ``evaluate_suite``.
    """
    combinations = expand_grid(base, grid)
    entries = [entry for _, entry in combinations]
    suites = [
        batch.evaluate_suite(
            paths,
            entries[start : start + CHUNK],
            metric=metric,
            trials={entry['name']: len(entries) for entry in entries[start : start + CHUNK]},
            **arguments,
        )
        for start in range(0, len(entries), CHUNK)
    ]
    from .evaluation import METRICS

    rows = sweep_table(combinations, suites, metric)
    ranked = [row for row in rows if row['mean'] is not None]
    best = None
    if ranked and METRICS.get(metric) is not None:
        choose = min if METRICS[metric] == 'lower' else max
        best = choose(ranked, key=lambda row: float(row['mean']))
    return copy_json(
        dict(
            format='market-lab-sweep-v1',
            metric=metric,
            grid={field: list(values) for field, values in grid.items()},
            combinations=len(entries),
            rows=rows,
            best=best,
            status=(
                'cancelled' if any(s.get('status') == 'cancelled' for s in suites) else 'completed'
            ),
            suites=suites,
        )
    )
