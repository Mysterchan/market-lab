"""Guards against backtest overfitting: deflated Sharpe, PBO and leak-free splits.

A researcher who tries many Agent prompts, rule parameters or model files and keeps the
best one will see a high Sharpe ratio by chance alone. These pure functions measure how
much of a result survives that selection:

- ``probabilistic_sharpe_ratio`` (PSR): the probability that the true Sharpe ratio
  exceeds a benchmark, allowing for sample length, skewness and fat tails.
- ``deflated_sharpe_ratio`` (DSR): PSR against the Sharpe ratio the best of ``n``
  worthless trials would be expected to reach.
- ``probability_of_backtest_overfitting`` (PBO): combinatorially symmetric
  cross-validation over a matrix of candidate returns.
- ``walk_forward_splits`` and ``purged_k_fold_splits``: train/test index ranges that
  never let a test period inform its own training data.

References: Bailey and López de Prado, "The Sharpe Ratio Efficient Frontier" (2012) and
"The Deflated Sharpe Ratio" (2014); Bailey, Borwein, López de Prado and Zhu, "The
Probability of Backtest Overfitting" (2017); López de Prado, "Advances in Financial
Machine Learning" (2018), chapter 7. Sharpe ratios here are per period (not annualised),
because the formulas are stated per observation.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
import math
from statistics import NormalDist
from typing import List, Optional, Sequence, Tuple

EULER_MASCHERONI = 0.5772156649015329
_NORMAL = NormalDist()

# A half-open index range [start, stop) into a return series.
IndexRange = Tuple[int, int]


def _moments(returns: Sequence[float]) -> Tuple[float, float, float, float]:
    """Mean, standard deviation (population), skewness and kurtosis (not excess)."""
    n = len(returns)
    mean = sum(returns) / n
    deviations = [r - mean for r in returns]
    variance = sum(d * d for d in deviations) / n
    if variance <= 0:
        return mean, 0.0, 0.0, 3.0
    sd = math.sqrt(variance)
    skew = sum(d**3 for d in deviations) / n / sd**3
    kurt = sum(d**4 for d in deviations) / n / variance**2
    return mean, sd, skew, kurt


def per_period_sharpe(returns: Sequence[float]) -> Optional[float]:
    """Mean over standard deviation of one return series; None when undefined."""
    if len(returns) < 2:
        return None
    mean, sd, _, _ = _moments(returns)
    return None if sd == 0 else mean / sd


def probabilistic_sharpe_ratio(
    returns: Sequence[float], benchmark_sharpe: float = 0.0
) -> Optional[float]:
    """Probability that the true per-period Sharpe ratio exceeds ``benchmark_sharpe``.

    Args:
        returns: Per-period returns (for example daily), at least three values.
        benchmark_sharpe: The per-period Sharpe ratio to beat; 0 tests for any skill.

    Returns:
        A probability in [0, 1], or None for fewer than three returns or zero variance.
    """
    if len(returns) < 3:
        return None
    mean, sd, skew, kurt = _moments(returns)
    if sd == 0:
        return None
    sharpe = mean / sd
    denominator = 1 - skew * sharpe + (kurt - 1) / 4 * sharpe**2
    if denominator <= 0:
        return None
    z = (sharpe - benchmark_sharpe) * math.sqrt(len(returns) - 1) / math.sqrt(denominator)
    return _NORMAL.cdf(z)


def expected_maximum_sharpe(trials: int, sharpe_variance: float) -> float:
    """Sharpe ratio the best of ``trials`` independent zero-skill strategies would reach.

    Args:
        trials: Number of configurations tried (prompts, parameters, model files).
        sharpe_variance: Variance of the per-period Sharpe ratios across those trials.

    Returns:
        The expected maximum per-period Sharpe ratio; 0 for a single trial.
    """
    if trials < 1:
        raise ValueError('trials must be at least 1')
    if trials == 1 or sharpe_variance <= 0:
        return 0.0
    first = _NORMAL.inv_cdf(1 - 1 / trials)
    second = _NORMAL.inv_cdf(1 - 1 / (trials * math.e))
    return math.sqrt(sharpe_variance) * ((1 - EULER_MASCHERONI) * first + EULER_MASCHERONI * second)


def deflated_sharpe_ratio(
    returns: Sequence[float],
    *,
    trial_sharpes: Optional[Sequence[float]] = None,
    trials: Optional[int] = None,
    sharpe_variance: Optional[float] = None,
) -> Optional[float]:
    """Probability that the selected strategy's Sharpe ratio is not a selection artefact.

    Give either the per-period Sharpe ratios of every trial (``trial_sharpes``), or the
    number of trials with the variance of their Sharpe ratios. A value above 0.95 is the
    usual threshold for a result that survives the search that produced it.

    Returns:
        A probability in [0, 1], or None when ``returns`` cannot define a Sharpe ratio.

    Raises:
        ValueError: If neither trial description is given.
    """
    if trial_sharpes is not None:
        values = list(trial_sharpes)
        trials = len(values)
        mean = sum(values) / trials if trials else 0.0
        sharpe_variance = sum((v - mean) ** 2 for v in values) / (trials - 1) if trials > 1 else 0.0
    if trials is None or sharpe_variance is None:
        raise ValueError('give trial_sharpes, or trials and sharpe_variance')
    return probabilistic_sharpe_ratio(returns, expected_maximum_sharpe(trials, sharpe_variance))


@dataclass(frozen=True)
class OverfittingEstimate:
    """Result of combinatorially symmetric cross-validation.

    ``probability`` is the share of splits where the in-sample winner ranked in the
    bottom half out of sample. ``logits`` holds each split's relative out-of-sample rank
    as log(w / (1 - w)); negative values are the overfit splits.
    """

    probability: float
    splits: int
    logits: Tuple[float, ...]


def probability_of_backtest_overfitting(
    returns_by_candidate: Sequence[Sequence[float]], blocks: int = 8
) -> OverfittingEstimate:
    """Estimate how often choosing the best backtest picks a below-median strategy.

    Args:
        returns_by_candidate: One equally long per-period return series for each
            candidate configuration (at least two candidates).
        blocks: Even number of contiguous blocks the time axis is cut into; every
            choice of half the blocks is one in-sample/out-of-sample split.

    Returns:
        The estimate over all C(blocks, blocks / 2) splits.

    Raises:
        ValueError: For fewer than two candidates, unequal lengths, an odd or too
            large ``blocks``.
    """
    candidates = [list(r) for r in returns_by_candidate]
    if len(candidates) < 2:
        raise ValueError('need at least two candidates')
    length = len(candidates[0])
    if any(len(r) != length for r in candidates):
        raise ValueError('every candidate needs the same number of returns')
    if blocks < 2 or blocks % 2 or blocks > length:
        raise ValueError('blocks must be even and no more than the number of returns')
    edges = [round(i * length / blocks) for i in range(blocks + 1)]

    def score(series: List[float], chosen: Sequence[int]) -> float:
        values = [v for b in chosen for v in series[edges[b] : edges[b + 1]]]
        sharpe = per_period_sharpe(values)
        return float('-inf') if sharpe is None else sharpe

    logits: List[float] = []
    count = len(candidates)
    for train in combinations(range(blocks), blocks // 2):
        test = [b for b in range(blocks) if b not in train]
        in_sample = [score(c, train) for c in candidates]
        best = max(range(count), key=lambda i: in_sample[i])
        out_of_sample = [score(c, test) for c in candidates]
        # Rank 1 is the worst; ties share the lower rank, which is conservative.
        rank = 1 + sum(1 for v in out_of_sample if v < out_of_sample[best])
        relative = rank / (count + 1)
        logits.append(math.log(relative / (1 - relative)))
    overfit = sum(1 for value in logits if value <= 0)
    return OverfittingEstimate(overfit / len(logits), len(logits), tuple(logits))


def walk_forward_splits(
    length: int,
    train: int,
    test: int,
    *,
    step: Optional[int] = None,
    anchored: bool = False,
    gap: int = 0,
) -> List[Tuple[IndexRange, IndexRange]]:
    """Successive train/test windows that always test after they train.

    Args:
        length: Number of observations.
        train: Training window length (the first window's length when ``anchored``).
        test: Test window length.
        step: How far each split moves forward; defaults to ``test``.
        anchored: Keep the training start at 0 so the window grows.
        gap: Observations skipped between training and test, for labels that look ahead.

    Returns:
        ((train_start, train_stop), (test_start, test_stop)) half-open ranges in order.
    """
    if min(train, test) < 1 or gap < 0:
        raise ValueError('train and test must be positive and gap not negative')
    step = test if step is None else step
    if step < 1:
        raise ValueError('step must be positive')
    splits = []
    start = 0
    while start + train + gap + test <= length:
        train_range = (0 if anchored else start, start + train)
        test_start = start + train + gap
        splits.append((train_range, (test_start, test_start + test)))
        start += step
    return splits


def purged_k_fold_splits(
    length: int, folds: int, *, label_horizon: int = 0, embargo: int = 0
) -> List[Tuple[List[int], IndexRange]]:
    """K contiguous test folds with leaking training observations removed.

    A label computed from the next ``label_horizon`` observations overlaps the test
    fold when it starts within that distance before the fold, so those observations
    are purged. ``embargo`` observations after the fold are dropped as well, because
    serial correlation lets them carry test information backwards.

    Returns:
        For each fold, the sorted training indices and the (start, stop) test range.
    """
    if folds < 2 or folds > length:
        raise ValueError('folds must be between 2 and the number of observations')
    if label_horizon < 0 or embargo < 0:
        raise ValueError('label_horizon and embargo must not be negative')
    edges = [round(i * length / folds) for i in range(folds + 1)]
    splits = []
    for k in range(folds):
        start, stop = edges[k], edges[k + 1]
        training = [i for i in range(length) if i + label_horizon < start or i >= stop + embargo]
        splits.append((training, (start, stop)))
    return splits
