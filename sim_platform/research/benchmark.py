"""A fixed, reproducible benchmark: synthetic markets in five regimes and set baselines.

Every researcher who runs ``evaluate_benchmark()`` with the same seed gets byte-identical
recordings, the same baselines and the same statistics, so results can be compared and
re-run by others. The recordings are generated (nothing to license or download) and dated
in the year 2100, so no model can remember them.

Regimes (one price-bar recording each, ``BENCHMARK_DAYS`` trading days):

- ``uptrend`` and ``downtrend``: steady drift with moderate noise.
- ``range``: mean reversion around a fixed level.
- ``volatile``: no drift, high noise.
- ``crash``: a calm rise, a sharp fall, then a slow partial recovery.

Baselines: buy and hold, a one-day rule, and a moving-average trend follower.
"""

from __future__ import annotations

import csv
from datetime import date, timedelta
import math
from pathlib import Path
from random import Random
from typing import Any, Dict, List, Union
import os

from sim_platform.domain.models import ContractError

PathInput = Union[str, 'os.PathLike[str]']

BENCHMARK_VERSION = 1
BENCHMARK_DAYS = 252
BENCHMARK_SEED = 2026
START_DATE = date(2100, 1, 4)  # a Monday
DAY_NS = 86_400 * 1_000_000_000
# Regime: (daily drift, daily volatility, mean reversion toward the start price).
REGIMES: Dict[str, tuple] = {
    'uptrend': (0.0008, 0.012, 0.0),
    'downtrend': (-0.0008, 0.012, 0.0),
    'range': (0.0, 0.010, 0.08),
    'volatile': (0.0, 0.030, 0.0),
    'crash': (None, 0.015, 0.0),
}
BUY_AND_HOLD = 'Buy and hold'

MOVING_AVERAGE = '''"""Benchmark baseline: hold shares while the close is above its recent average."""

from statistics import mean

SETTINGS = dict(window=20, shares=100)


def setup(parameters):
    SETTINGS.update(parameters)


def decide(bars, account, context):
    window = int(SETTINGS['window'])
    if len(bars) <= window:
        return None
    closes = [bar['close'] for bar in bars]
    above = closes[-1] > mean(closes[-window - 1 : -1])
    return dict(target_position=int(SETTINGS['shares']) if above else 0)
'''


def _crash_drift(day: int, days: int) -> float:
    if day < days * 0.4:
        return 0.001
    if day < days * 0.45:
        return -0.03
    return 0.0006


def _weekdays(count: int) -> List[date]:
    days: List[date] = []
    current = START_DATE
    while len(days) < count:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    return days


def write_benchmark(
    folder: PathInput, *, seed: int = BENCHMARK_SEED, days: int = BENCHMARK_DAYS
) -> Dict[str, Any]:
    """Write the benchmark recordings and the baseline model file into ``folder``.

    Returns:
        ``recordings`` (regime name to CSV path), ``model_file`` and the ``version``,
        ``seed`` and ``days`` that identify this benchmark.
    """
    root = Path(folder).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    recordings = {}
    for index, (regime, (drift, volatility, reversion)) in enumerate(REGIMES.items()):
        rng = Random(seed * 100 + index)
        path = root / f'benchmark-{regime}.csv'
        price = 100.0
        with path.open('w', newline='', encoding='utf-8') as stream:
            writer = csv.writer(stream)
            writer.writerow(['Date', 'Open', 'High', 'Low', 'Close', 'Volume'])
            for day, when in enumerate(_weekdays(days)):
                mu = _crash_drift(day, days) if drift is None else drift
                pull = reversion * math.log(100.0 / price)
                opened = price
                price = max(1.0, price * math.exp(mu + pull + volatility * rng.gauss(0, 1)))
                high = max(opened, price) * (1 + abs(rng.gauss(0, volatility / 3)))
                low = min(opened, price) * (1 - abs(rng.gauss(0, volatility / 3)))
                volume = int(1_000_000 * (1 + abs(rng.gauss(0, 0.5))))
                writer.writerow(
                    [
                        when.isoformat(),
                        f'{opened:.2f}',
                        f'{high:.2f}',
                        f'{low:.2f}',
                        f'{price:.2f}',
                        volume,
                    ]
                )
        recordings[regime] = str(path)
    model = root / 'benchmark_moving_average.py'
    model.write_text(MOVING_AVERAGE, encoding='utf-8')
    return dict(
        version=BENCHMARK_VERSION,
        seed=seed,
        days=days,
        recordings=recordings,
        model_file=str(model),
    )


def baseline_entries(model_file: str) -> List[Dict[str, Any]]:
    """The benchmark's fixed baselines as evaluate_suite() entries."""
    return [
        dict(name=BUY_AND_HOLD, policy='rule', hold_ns=BENCHMARK_DAYS * 2 * DAY_NS),
        dict(name='One-day rule', policy='rule', hold_ns=DAY_NS),
        dict(name='Moving average (20 days)', policy='model', model=dict(model_file=model_file)),
    ]


def evaluate_benchmark(
    batch: Any, folder: PathInput, entries: List[Dict[str, Any]], **arguments: Any
) -> Dict[str, Any]:
    """Run ``entries`` and the baselines on every benchmark recording and evaluate them.

    ``batch`` is an open ResearchBatch. Entries use the evaluate_suite() format; the
    baseline entry for the tests is buy and hold unless ``baseline`` is given. Other
    arguments (profile, cash, execution settings, ...) go to evaluate_suite();
    ``regimes`` limits the run to some regimes. A full run plays every entry on all five
    recordings, which takes minutes per entry. The report's ``benchmark`` field records
    the version, seed, length and regimes.
    """
    written = write_benchmark(
        folder,
        seed=arguments.pop('benchmark_seed', BENCHMARK_SEED),
        days=arguments.pop('benchmark_days', BENCHMARK_DAYS),
    )
    arguments.setdefault('baseline', BUY_AND_HOLD)
    arguments.setdefault('symbol', 'BENCH')
    regimes = arguments.pop('regimes', None) or list(REGIMES)
    unknown = set(regimes) - set(REGIMES)
    if unknown:
        raise ContractError('unknown benchmark regimes: ' + ', '.join(sorted(unknown)))
    report = batch.evaluate_suite(
        [written['recordings'][name] for name in regimes],
        baseline_entries(written['model_file']) + list(entries),
        **arguments,
    )
    report['benchmark'] = dict(
        {k: written[k] for k in ('version', 'seed', 'days')}, regimes=list(regimes)
    )
    return report
