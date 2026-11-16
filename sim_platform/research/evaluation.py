"""Compare saved runs of Agents and baselines with distribution-free statistics.

An evaluation groups saved run reports into named entries ("Agent v3", "Buy and hold",
"LSTM"), checks that every run traded the same market under the same account and
execution settings, and reports each entry's figures with bootstrap confidence intervals
and a permutation test against a baseline entry. Design and definitions:
docs/architecture/agent-evaluation-design.md and docs/user-guide/evaluation.md.
"""

from __future__ import annotations

from itertools import combinations
from math import comb
from random import Random
from statistics import mean, stdev
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from sim_platform.domain.json_values import copy_json
from sim_platform.domain.models import ContractError

from .metrics import performance
from .overfitting import (
    per_period_sharpe,
    probabilistic_sharpe_ratio,
    deflated_sharpe_ratio,
    probability_of_backtest_overfitting,
)
from .views import session_view

# A detached run report (research.reports.format_report).
Report = Dict[str, Any]

FORMAT = 'agent-evaluation-v1'
DEFAULT_METRIC = 'total_return'
DEFAULT_SEED = 2026
DEFAULT_RESAMPLES = 10_000
CONFIDENCE = 0.95
# Entries with fewer values than this get their interval and test marked insufficient.
MIN_RELIABLE_RUNS = 5
MAX_ENTRIES = 20
MAX_RUNS_PER_ENTRY = 1_000

# Figures computed for every run. Value: 'higher' or 'lower' is better, or None.
METRICS: Dict[str, Optional[str]] = {
    'total_return': 'higher',
    'max_drawdown_ratio': 'lower',
    'sharpe_ratio': 'higher',
    'sortino_ratio': 'higher',
    'win_rate': 'higher',
    'turnover': None,
    'exposure': None,
    'round_trips': None,
    'fees': 'lower',
    # Agent runs only (None for other methods): from the Agent's operation log.
    'model_calls': None,
    'failed_calls': 'lower',
    'input_tokens': 'lower',
    'output_tokens': 'lower',
    'mean_latency_seconds': 'lower',
    'model_cost': 'lower',
}
# Figures read from a run's model usage rather than its trading.
MODEL_FIGURES = (
    'model_calls',
    'failed_calls',
    'input_tokens',
    'output_tokens',
    'mean_latency_seconds',
    'model_cost',
)
NS_PER_SECOND = 1_000_000_000

# research_source keys that identify the market a run traded: the recording file(s),
# news, synthetic-book options, disguise and the prepared starting position. Other keys
# (where the prepared copy lives, Live or Controlled pacing, Agent bookkeeping) may differ.
_SOURCE_IDENTITY = (
    'path',
    'paths',
    'universe',
    'bytes',
    'mtime_ns',
    'news_identity',
    'book',
    'disguise',
    'start_origin',
)


def agent_model_usage(archive: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Model calls, failures, tokens and reply latency from an Agent's sealed log.

    Calls count dispatched attempts (retries included); failures count attempts that
    ended in a retry or a final failure. Tokens add up each reply's reported usage; they
    are None when no reply reported any. Latency is market time from dispatch to receipt,
    which in Live mode is wall-clock time; None for logs written before dispatch times
    were recorded. ``by_label`` splits replies and tokens by the role label an Agent gave
    its requests (TradingAgent ``label``). ``budget_exceeded`` (only when it happened)
    holds what was spent when the run's model budget ended it. Returns None without a log.
    """
    if not archive:
        return None
    dispatched: Dict[str, int] = {}
    latencies: List[int] = []
    calls = failed = 0
    tokens = dict(input_tokens=0, output_tokens=0)
    reported = False
    served: List[str] = []
    by_label: Dict[str, Dict[str, int]] = {}
    budget: Optional[Dict[str, Any]] = None
    for record in archive.get('records') or []:
        if record.get('kind') != 'control':
            continue
        event, details = record.get('event'), record.get('details') or {}
        if event == 'budget_exceeded':
            budget = dict(details)
        elif event == 'attempt_dispatched':
            calls += 1
            if details.get('dispatched_ns') is not None:
                dispatched[str(details['attempt_id'])] = int(details['dispatched_ns'])
        elif event in ('attempt_retry', 'attempt_failed'):
            failed += 1
        elif event == 'attempt_completed':
            usage = (details.get('result') or {}).get('usage') or {}
            if isinstance(usage.get('model'), str) and usage['model'] not in served:
                served.append(usage['model'])
            if isinstance(usage.get('label'), str):
                role = by_label.setdefault(
                    usage['label'], dict(replies=0, input_tokens=0, output_tokens=0)
                )
                role['replies'] += 1
                for key in ('input_tokens', 'output_tokens'):
                    role[key] += int(usage.get(key, 0))
            if usage:
                reported = True
                for key in tokens:
                    tokens[key] += int(usage.get(key, 0))
            start = dispatched.get(str(details.get('attempt_id')))
            if start is not None and details.get('received_ns') is not None:
                latencies.append(int(details['received_ns']) - start)
    summary = dict(
        model_calls=calls,
        failed_calls=failed,
        input_tokens=tokens['input_tokens'] if reported else None,
        output_tokens=tokens['output_tokens'] if reported else None,
        mean_latency_seconds=(
            sum(latencies) / len(latencies) / NS_PER_SECOND if latencies else None
        ),
        served_models=served,
        by_label=by_label,
    )
    if budget is not None:
        summary['budget_exceeded'] = budget
    return summary


def model_cost(
    usage: Optional[Dict[str, Any]], prices: Optional[Mapping[str, Any]]
) -> Optional[float]:
    """USD cost of a run's tokens at ``prices`` ('input' and 'output' USD per million)."""
    if not usage or not prices or usage.get('input_tokens') is None:
        return None
    return (
        usage['input_tokens'] * float(prices.get('input', 0))
        + usage['output_tokens'] * float(prices.get('output', 0))
    ) / 1_000_000


def agent_pacing(report: Report) -> Optional[str]:
    """'live' or 'turn' for an Agent run (its settings' speed), None for other methods."""
    agent = (report['run'].get('config') or {}).get('agent') or {}
    if not agent:
        return None
    return 'turn' if agent.get('speed') == 'turn' else 'live'


def run_figures(report: Report, prices: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """The evaluation figures of one saved run report (each None when not computable).

    Model figures come from report['model_usage'] (agent_model_usage), which evaluations
    attach to Agent runs; ``prices`` gives the model's USD price per million tokens.
    """
    view = session_view(dict(mode='saved', report=report))
    result = performance(view)
    summary = report.get('summary') or {}
    figures = {name: result.get(name) for name in METRICS if name not in MODEL_FIGURES}
    figures['fees'] = summary.get('run_fees')
    usage = report.get('model_usage')
    for name in MODEL_FIGURES[:-1]:
        figures[name] = (usage or {}).get(name)
    figures['model_cost'] = model_cost(usage, prices)
    if agent_pacing(report) == 'turn':
        # The market waits for the model, so reply time is not measured in market time.
        figures['mean_latency_seconds'] = None
    return figures


def served_models(reports: Sequence[Report]) -> List[str]:
    """Every model version the provider reported serving across the reports' Agent runs."""
    return sorted(
        {
            model
            for report in reports
            for model in ((report.get('model_usage') or {}).get('served_models') or [])
        }
    )


def run_period_returns(report: Report) -> Optional[List[float]]:
    """Returns between consecutive points of a run's equity curve (one per archived bar).

    None when the run has no curve (see metrics.performance) or equity is not positive.
    """
    series = performance(session_view(dict(mode='saved', report=report))).get('series')
    if not series or len(series) < 2:
        return None
    equity = [point[1] for point in series]
    if any(value <= 0 for value in equity[:-1]):
        return None
    return [after / before - 1 for before, after in zip(equity, equity[1:])]


def entry_returns(runs: Sequence[Optional[List[float]]]) -> Optional[List[float]]:
    """An entry's return series: the per-period mean of its runs' returns.

    Only runs with the most common series length take part, so every period averages
    the same runs. None when no run has a series.
    """
    usable = [r for r in runs if r]
    if not usable:
        return None
    lengths = [len(r) for r in usable]
    length = max(set(lengths), key=lambda n: (lengths.count(n), n))
    chosen = [r for r in usable if len(r) == length]
    return [sum(r[i] for r in chosen) / len(chosen) for i in range(length)]


def selection_statistics(
    series: Mapping[str, Optional[List[float]]], trials: Mapping[str, int]
) -> Tuple[Dict[str, Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Per-entry PSR / deflated Sharpe, and PBO across entries (research.overfitting).

    ``trials`` gives, per entry, how many configurations were tried before this one was
    kept (prompts, parameters, revisions); entries not named count as one trial. The
    variance of Sharpe ratios across the evaluated entries stands in for the variance
    across those trials. PBO treats the entries as the candidates a researcher chose
    between; it needs two or more entries with equally long series of 4+ periods.
    """
    sharpes = {
        name: per_period_sharpe(values) if values else None for name, values in series.items()
    }
    known = [v for v in sharpes.values() if v is not None]
    spread = (
        sum((v - mean(known)) ** 2 for v in known) / (len(known) - 1) if len(known) > 1 else 0.0
    )
    per_entry: Dict[str, Dict[str, Any]] = {}
    for name, values in series.items():
        count = trials.get(name, 1)
        per_entry[name] = dict(
            periods=len(values) if values else 0,
            sharpe_per_period=sharpes[name],
            probabilistic_sharpe=probabilistic_sharpe_ratio(values) if values else None,
            trials=count,
            deflated_sharpe=(
                deflated_sharpe_ratio(values, trials=count, sharpe_variance=spread)
                if values
                else None
            ),
        )
    candidates = [v for v in series.values() if v]
    overfitting = None
    if len(candidates) >= 2 and len({len(v) for v in candidates}) == 1 and len(candidates[0]) >= 4:
        blocks = min(8, len(candidates[0]) - len(candidates[0]) % 2)
        estimate = probability_of_backtest_overfitting(candidates, blocks=blocks)
        overfitting = dict(
            probability=estimate.probability,
            splits=estimate.splits,
            blocks=blocks,
            candidates=len(candidates),
        )
    return per_entry, overfitting


def _execution_settings(saved: Optional[Mapping[str, Any]]) -> Optional[Dict[str, Any]]:
    """Saved execution settings with defaults filled in for fields added later.

    A run saved before latency jitter or impact existed then compares equal to a new run
    that leaves them at their defaults.
    """
    if saved is None:
        return None
    from dataclasses import asdict, fields

    from sim_platform.execution import ExecutionConfig

    defaults = asdict(ExecutionConfig())
    known = {f.name for f in fields(ExecutionConfig)}
    return dict(defaults, **{k: v for k, v in saved.items() if k in known})


def comparable_settings(report: Report) -> Dict[str, Any]:
    """The settings that must match between runs for their results to be compared."""
    run = report['run']
    config = run.get('config') or {}
    source = config.get('research_source') or {}
    prepared = source.get('prepared') or {}
    initial = run.get('initial_snapshot') or {}
    return copy_json(
        dict(
            universe=run.get('universe'),
            source={k: source.get(k) for k in _SOURCE_IDENTITY},
            book_depth=prepared.get('depth'),
            starting_cash=initial.get('cash'),
            starting_positions=initial.get('positions'),
            execution=_execution_settings(config.get('execution')),
            short_selling=config.get('short_selling'),
        )
    )


def run_window(report: Report) -> Dict[str, Any]:
    """Market time a run covered: its start and its chosen end (None: end of recording).

    An Agent's end is its settings' end_ns; a Rule or model-file run stopped early by a
    suite saves end_time_ns with its source.
    """
    config = report['run'].get('config') or {}
    source = config.get('research_source') or {}
    start = source.get('experiment_start_ns', (source.get('start_origin') or {}).get('time_ns'))
    end = (config.get('agent') or {}).get('end_ns', source.get('end_time_ns'))
    return dict(start_ns=start, end_ns=int(end) if end is not None else None)


def _difference(expected: Any, actual: Any, path: str = '') -> Optional[str]:
    """The first dotted key at which two JSON values differ, or None when equal."""
    if isinstance(expected, dict) and isinstance(actual, dict):
        for key in sorted(set(expected) | set(actual)):
            found = _difference(
                expected.get(key), actual.get(key), f'{path}.{key}' if path else key
            )
            if found is not None:
                return found
        return None
    return None if expected == actual else (path or 'value')


def check_comparable(reports: Sequence[Tuple[str, Report]]) -> Dict[str, Any]:
    """Return the shared settings, or raise ContractError naming the first difference."""
    first_name, first = reports[0]
    expected = comparable_settings(first)
    for name, report in reports[1:]:
        found = _difference(expected, comparable_settings(report))
        if found is not None:
            raise ContractError(
                f'runs are not comparable: {found} differs between run '
                f'{first["run"]["id"]} ({first_name}) and run {report["run"]["id"]} ({name})'
            )
    if expected['starting_positions']:
        raise ContractError('runs that start with positions held cannot be evaluated')
    pacings = {agent_pacing(report) for _, report in reports} - {None}
    if len(pacings) > 1:
        raise ContractError(
            'runs are not comparable: Live and turn-based Agent runs answer different '
            'questions; evaluate them separately'
        )
    return expected


def data_dates(report: Report) -> Optional[Tuple[str, str]]:
    """(first, last) calendar date of a price-bar run's data, or None when unknown.

    The first date is the prepared recording's; the last adds the highest market day
    with a daily close. ITCH recordings carry no date and give None.
    """
    from datetime import date, timedelta

    source = (report['run'].get('config') or {}).get('research_source') or {}
    calendar = (source.get('prepared') or {}).get('calendar') or {}
    first = calendar.get('start_date')
    if not isinstance(first, str):
        return None
    market_data = report.get('market_data') or {}
    closes = list(market_data.get('daily_closes') or [])
    for market in (market_data.get('markets') or {}).values():
        closes.extend(market.get('daily_closes') or [])
    last_day = max((int(day) for day, _, _ in closes), default=0)
    start = date.fromisoformat(first)
    return first, (start + timedelta(days=last_day)).isoformat()


def is_agent_run(report: Report) -> bool:
    return bool((report['run'].get('config') or {}).get('agent'))


def memorisation_flags(report: Report, cutoff: Optional[str]) -> List[str]:
    """Warnings about whether an Agent's model may have seen this run's market data.

    ``cutoff`` is the model's training-data cutoff (YYYY-MM-DD) or None when unknown.
    Rule, manual and model-file runs get no flag. The flags are 'disguised',
    'after-cutoff', 'possible-memorisation', 'cutoff-unknown' and 'dates-unknown'.
    """
    from datetime import date

    if not is_agent_run(report):
        return []
    dates = data_dates(report)
    if dates is None:
        return ['dates-unknown']
    if date.fromisoformat(dates[0]) > date.today():
        # Dates no model can have seen: a disguised recording (Platform.disguise_recording).
        return ['disguised']
    if cutoff is None:
        return ['cutoff-unknown']
    try:
        limit = date.fromisoformat(cutoff)
    except (TypeError, ValueError):
        raise ContractError(f'training cutoff {cutoff!r} is not a YYYY-MM-DD date') from None
    if date.fromisoformat(dates[0]) > limit:
        return ['after-cutoff']
    return ['possible-memorisation']


# --- Statistics -------------------------------------------------------------------


def _percentile(sorted_values: List[float], fraction: float) -> float:
    """Linear-interpolation percentile of already sorted values."""
    position = (len(sorted_values) - 1) * fraction
    low = int(position)
    high = min(low + 1, len(sorted_values) - 1)
    return sorted_values[low] + (sorted_values[high] - sorted_values[low]) * (position - low)


def _interval(samples: List[float]) -> List[float]:
    samples.sort()
    tail = (1 - CONFIDENCE) / 2
    return [_percentile(samples, tail), _percentile(samples, 1 - tail)]


def bootstrap_mean_interval(values: Sequence[float], rng: Random, resamples: int) -> List[float]:
    """95% percentile bootstrap interval of the mean."""
    n = len(values)
    if n == 1 or len(set(values)) == 1:
        return [float(values[0]), float(values[0])]
    return _interval([sum(rng.choices(values, k=n)) / n for _ in range(resamples)])


def bootstrap_difference_interval(
    values: Sequence[float], baseline: Sequence[float], rng: Random, resamples: int
) -> List[float]:
    """95% percentile bootstrap interval of mean(values) − mean(baseline), resampled apart."""
    n, m = len(values), len(baseline)
    return _interval(
        [
            sum(rng.choices(values, k=n)) / n - sum(rng.choices(baseline, k=m)) / m
            for _ in range(resamples)
        ]
    )


def permutation_p_value(
    values: Sequence[float], baseline: Sequence[float], rng: Random, resamples: int
) -> Tuple[float, bool]:
    """(two-sided p-value, exact) for a difference in means, by relabelling the runs.

    Exact over every split when there are at most ``resamples`` of them; otherwise
    ``resamples`` random splits with the (count + 1) / (resamples + 1) estimate.
    """
    pooled = list(values) + list(baseline)
    n, total = len(values), sum(pooled)
    m = len(pooled) - n
    observed = abs(sum(values) / n - (total - sum(values)) / m)
    tolerance = 1e-12 * max(1.0, observed)

    def extreme(chosen_sum: float) -> bool:
        return abs(chosen_sum / n - (total - chosen_sum) / m) >= observed - tolerance

    if comb(len(pooled), n) <= resamples:
        splits = list(combinations(pooled, n))
        return sum(extreme(sum(s)) for s in splits) / len(splits), True
    hits = sum(extreme(sum(rng.sample(pooled, n))) for _ in range(resamples))
    return (hits + 1) / (resamples + 1), False


def sign_flip_p_value(
    differences: Sequence[float], rng: Random, resamples: int
) -> Tuple[float, bool]:
    """(two-sided p-value, exact) that differences from a constant average zero."""
    n = len(differences)
    observed = abs(sum(differences))
    tolerance = 1e-12 * max(1.0, observed)
    if 2**n <= resamples:
        hits = 0
        for mask in range(2**n):
            flipped = sum(-d if mask >> i & 1 else d for i, d in enumerate(differences))
            hits += abs(flipped) >= observed - tolerance
        return hits / 2**n, True
    hits = sum(
        abs(sum(d if rng.random() < 0.5 else -d for d in differences)) >= observed - tolerance
        for _ in range(resamples)
    )
    return (hits + 1) / (resamples + 1), False


def holm_adjust(p_values: Sequence[Optional[float]]) -> List[Optional[float]]:
    """Holm step-down adjusted p-values; None entries are skipped and stay None."""
    indexed = sorted((p, i) for i, p in enumerate(p_values) if p is not None)
    adjusted: List[Optional[float]] = [None] * len(p_values)
    running, count = 0.0, len(indexed)
    for rank, (p, index) in enumerate(indexed):
        running = max(running, min(1.0, (count - rank) * p))
        adjusted[index] = running
    return adjusted


def describe(values: Sequence[float], rng: Random, resamples: int) -> Dict[str, Any]:
    """n, mean, sample standard deviation, range and bootstrap interval of the mean."""
    if not values:
        return dict(n=0, mean=None, stdev=None, min=None, max=None, interval=None)
    return dict(
        n=len(values),
        mean=mean(values),
        stdev=stdev(values) if len(values) > 1 else None,
        min=min(values),
        max=max(values),
        interval=bootstrap_mean_interval(values, rng, resamples),
        insufficient=len(values) < MIN_RELIABLE_RUNS and len(set(values)) > 1,
    )


def compare_with_baseline(
    values: Sequence[float], baseline: Sequence[float], rng: Random, resamples: int
) -> Dict[str, Any]:
    """Difference in means against the baseline, its interval and a two-sided p-value.

    A baseline whose values are all equal (one run, or a deterministic method) is a
    constant: the interval resamples only ``values`` and the test is a sign-flip test of
    values minus that constant. When the entry's values are all equal too, the
    difference is exact and no test is made (p_value None, ``deterministic`` True).
    Otherwise both groups are resampled and the test relabels runs between them.
    """
    if not values or not baseline:
        return dict(difference=None, interval=None, p_value=None, test=None, exact=None)
    constant = len(set(baseline)) == 1
    if constant and len(set(values)) == 1:
        # Neither side varies: the difference is exact and no test applies.
        difference = values[0] - baseline[0]
        return dict(
            difference=difference,
            interval=[difference, difference],
            p_value=None,
            test='none: both constant',
            exact=True,
            insufficient=False,
            deterministic=True,
        )
    if constant:
        differences = [v - baseline[0] for v in values]
        interval = bootstrap_mean_interval(differences, rng, resamples)
        p_value, exact = sign_flip_p_value(differences, rng, resamples)
        test = 'sign-flip against a constant baseline'
    else:
        interval = bootstrap_difference_interval(values, baseline, rng, resamples)
        p_value, exact = permutation_p_value(values, baseline, rng, resamples)
        test = 'permutation'
    return dict(
        difference=mean(values) - mean(baseline),
        interval=interval,
        p_value=p_value,
        test=test,
        exact=exact,
        insufficient=len(values) < MIN_RELIABLE_RUNS
        or (not constant and len(baseline) < MIN_RELIABLE_RUNS),
    )


# --- Report -----------------------------------------------------------------------


def evaluate_reports(
    entries: Mapping[str, Sequence[Report]],
    *,
    baseline: Optional[str] = None,
    metric: str = DEFAULT_METRIC,
    seed: int = DEFAULT_SEED,
    resamples: int = DEFAULT_RESAMPLES,
    flags: Optional[Mapping[str, List[str]]] = None,
    token_prices: Optional[Mapping[str, Any]] = None,
    trials: Optional[Mapping[str, int]] = None,
) -> Dict[str, Any]:
    """Build an evaluation report from named groups of saved run reports.

    ``baseline`` names the entry every other entry is tested against (default: none,
    figures only). ``metric`` is the figure tested. ``token_prices`` ('input' and 'output'
    USD per million tokens) prices Agent runs' model usage. ``flags`` maps a run id to warnings
    shown with that run (see memorisation_flags). ``trials`` maps an entry name to the
    number of configurations tried to arrive at it, for the deflated Sharpe ratio.
    """
    if not isinstance(entries, Mapping) or not 1 <= len(entries) <= MAX_ENTRIES:
        raise ContractError(f'an evaluation needs 1..{MAX_ENTRIES} entries')
    if metric not in METRICS:
        raise ContractError(f'unknown metric {metric!r}; choose one of {", ".join(METRICS)}')
    if baseline is not None and baseline not in entries:
        raise ContractError(f'baseline {baseline!r} is not one of the entries')
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise ContractError('seed must be an integer')
    if not isinstance(resamples, int) or not 100 <= resamples <= 1_000_000:
        raise ContractError('resamples must be an integer from 100 to 1000000')
    pairs: List[Tuple[str, Report]] = []
    for name, reports in entries.items():
        if not isinstance(name, str) or not name.strip():
            raise ContractError('entry names must be non-empty text')
        if not 1 <= len(reports) <= MAX_RUNS_PER_ENTRY:
            raise ContractError(f'entry {name!r} needs 1..{MAX_RUNS_PER_ENTRY} runs')
        pairs.extend((name, report) for report in reports)
    ids = [report['run']['id'] for _, report in pairs]
    if len(set(ids)) != len(ids):
        raise ContractError('a run appears more than once in the evaluation')
    shared = check_comparable(pairs)
    trials = dict(trials or {})
    if set(trials) - set(entries) or not all(
        isinstance(v, int) and not isinstance(v, bool) and v >= 1 for v in trials.values()
    ):
        raise ContractError('trials must map entry names to positive integers')
    rng = Random(seed)
    flags = flags or {}
    windows = {report['run']['id']: run_window(report) for _, report in pairs}
    warnings: List[str] = []
    if len({(w['start_ns'], w['end_ns']) for w in windows.values()}) > 1:
        warnings.append(
            'Runs cover different market-time windows (an Agent stops at its chosen end; '
            'other methods run to the end of the recording). Compare with care.'
        )
    unflagged = [
        report['run']['id']
        for _, report in pairs
        if set(flags.get(report['run']['id'], [])) & {'possible-memorisation', 'cutoff-unknown'}
    ]
    if unflagged:
        warnings.append(
            f'{len(unflagged)} Agent run(s) used real market dates that the model may have '
            'seen in training. Disguise the recording to rule this out.'
        )
    if token_prices is not None and (
        not isinstance(token_prices, Mapping)
        or set(token_prices) - {'input', 'output'}
        or not all(
            isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0
            for v in token_prices.values()
        )
    ):
        raise ContractError("token_prices maps 'input' and 'output' to USD per million tokens")
    figures = {
        name: [(report['run']['id'], run_figures(report, token_prices)) for report in reports]
        for name, reports in entries.items()
    }

    def values(name: str, figure: str) -> List[float]:
        return [f[figure] for _, f in figures[name] if f[figure] is not None]

    selection, overfitting = selection_statistics(
        {
            name: entry_returns([run_period_returns(r) for r in reports])
            for name, reports in entries.items()
        },
        trials,
    )
    rows: List[Dict[str, Any]] = []
    for name in entries:
        rows.append(
            dict(
                name=name,
                pacing=next(
                    (agent_pacing(r) for r in entries[name] if agent_pacing(r) is not None), None
                ),
                runs=[
                    dict(
                        run_id=run_id,
                        figures=f,
                        window=windows[run_id],
                        flags=list(flags.get(run_id, [])),
                    )
                    for run_id, f in figures[name]
                ],
                statistics={
                    figure: dict(
                        describe(values(name, figure), rng, resamples),
                        missing=sum(1 for _, f in figures[name] if f[figure] is None),
                    )
                    for figure in METRICS
                },
                comparison=None,
                selection=selection[name],
                served_models=served_models(entries[name]),
            )
        )
        if len(rows[-1]['served_models']) > 1:
            warnings.append(
                f'Entry {name!r} was served by several model versions '
                f'({", ".join(rows[-1]["served_models"])}); its runs are not repeats of one model.'
            )
    if baseline is not None:
        others = [row for row in rows if row['name'] != baseline]
        base = values(baseline, metric)
        for row in others:
            row['comparison'] = compare_with_baseline(
                values(row['name'], metric), base, rng, resamples
            )
        adjusted = holm_adjust([row['comparison']['p_value'] for row in others])
        for row, p in zip(others, adjusted):
            row['comparison']['p_value_holm'] = p
    return copy_json(
        dict(
            format=FORMAT,
            metric=metric,
            better=dict(METRICS),
            baseline=baseline,
            seed=seed,
            resamples=resamples,
            confidence=CONFIDENCE,
            min_reliable_runs=MIN_RELIABLE_RUNS,
            settings=shared,
            token_prices=dict(token_prices) if token_prices else None,
            entries=rows,
            overfitting=overfitting,
            warnings=warnings,
            notes=[
                'Repeated runs on one recording measure a method\'s own randomness on that '
                'market, not market uncertainty; compare recordings for robustness.',
                'Intervals are 95% percentile bootstrap intervals of the mean; p-values are '
                'two-sided and Holm-adjusted across entries.',
                'Selection figures use per-bar returns of each entry\'s mean equity curve. '
                'A deflated Sharpe above 0.95 survives the number of trials given; PBO is '
                'the chance that the best entry in sample is below median out of sample.',
            ],
        )
    )


def latency_cost(
    live: Mapping[str, Any],
    turn: Mapping[str, Any],
    entry: str,
    *,
    turn_entry: Optional[str] = None,
    metric: str = DEFAULT_METRIC,
    seed: int = DEFAULT_SEED,
    resamples: int = DEFAULT_RESAMPLES,
) -> Dict[str, Any]:
    """What an Agent's reaction time cost: its Live mean minus its turn-based mean.

    ``live`` and ``turn`` are evaluation reports of the same Agent on the same market and
    window, one Live and one turn-based; ``entry`` names it in ``live`` (and in ``turn``
    unless ``turn_entry`` is given). A negative cost on a higher-is-better metric means
    thinking time lost money. The interval is a 95% bootstrap interval of the difference.
    """

    def values(report: Mapping[str, Any], name: str, pacing: str) -> List[float]:
        row = next((e for e in report['entries'] if e['name'] == name), None)
        if row is None:
            raise ContractError(f'entry {name!r} is not in the evaluation')
        if row.get('pacing') != pacing:
            raise ContractError(f'entry {name!r} is not a {pacing} Agent entry')
        found = [r['figures'][metric] for r in row['runs'] if r['figures'][metric] is not None]
        if not found:
            raise ContractError(f'entry {name!r} has no {metric} values')
        return found

    if metric not in METRICS:
        raise ContractError(f'unknown metric {metric!r}')
    if live['settings'] != turn['settings']:
        raise ContractError('the Live and turn-based evaluations used different markets')
    live_values = values(live, entry, 'live')
    turn_values = values(turn, turn_entry or entry, 'turn')
    rng = Random(seed)
    return copy_json(
        dict(
            metric=metric,
            live_mean=mean(live_values),
            turn_mean=mean(turn_values),
            cost=mean(live_values) - mean(turn_values),
            interval=bootstrap_difference_interval(live_values, turn_values, rng, resamples),
            runs=dict(live=len(live_values), turn=len(turn_values)),
        )
    )
