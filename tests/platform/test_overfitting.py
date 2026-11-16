import math
from random import Random
import unittest

from sim_platform.research.overfitting import (
    deflated_sharpe_ratio,
    expected_maximum_sharpe,
    per_period_sharpe,
    probabilistic_sharpe_ratio,
    probability_of_backtest_overfitting,
    purged_k_fold_splits,
    walk_forward_splits,
)


def noise(seed, count, drift=0.0):
    rng = Random(seed)
    return [rng.gauss(drift, 0.01) for _ in range(count)]


class SharpeTests(unittest.TestCase):
    def test_probabilistic_sharpe_rises_with_skill_and_length(self):
        weak = probabilistic_sharpe_ratio(noise(1, 60, 0.0005))
        strong = probabilistic_sharpe_ratio(noise(1, 600, 0.002))
        self.assertLess(weak, strong)
        self.assertGreater(strong, 0.99)

    def test_probabilistic_sharpe_is_half_at_the_benchmark(self):
        returns = noise(2, 500, 0.001)
        sharpe = per_period_sharpe(returns)
        self.assertAlmostEqual(probabilistic_sharpe_ratio(returns, sharpe), 0.5, places=9)

    def test_undefined_inputs_return_none(self):
        self.assertIsNone(probabilistic_sharpe_ratio([0.01, 0.02]))
        self.assertIsNone(probabilistic_sharpe_ratio([0.01] * 10))
        self.assertIsNone(per_period_sharpe([0.01]))

    def test_expected_maximum_grows_with_trials(self):
        self.assertEqual(expected_maximum_sharpe(1, 0.01), 0.0)
        ten = expected_maximum_sharpe(10, 0.01)
        thousand = expected_maximum_sharpe(1000, 0.01)
        self.assertGreater(ten, 0)
        self.assertGreater(thousand, ten)
        # Known value: 1,000 trials with unit variance expect a maximum near 3.25.
        self.assertAlmostEqual(expected_maximum_sharpe(1000, 1.0), 3.25, delta=0.02)

    def test_deflation_lowers_confidence_after_many_trials(self):
        returns = noise(3, 250, 0.001)
        one = deflated_sharpe_ratio(returns, trials=1, sharpe_variance=0.0)
        many = deflated_sharpe_ratio(returns, trials=200, sharpe_variance=0.004)
        self.assertAlmostEqual(one, probabilistic_sharpe_ratio(returns))
        self.assertLess(many, one)

    def test_trial_sharpes_and_summary_agree(self):
        returns = noise(4, 250, 0.001)
        trials = [per_period_sharpe(noise(s, 250)) for s in range(20)]
        mean = sum(trials) / len(trials)
        variance = sum((t - mean) ** 2 for t in trials) / (len(trials) - 1)
        self.assertAlmostEqual(
            deflated_sharpe_ratio(returns, trial_sharpes=trials),
            deflated_sharpe_ratio(returns, trials=20, sharpe_variance=variance),
        )
        with self.assertRaises(ValueError):
            deflated_sharpe_ratio(returns)


class OverfittingTests(unittest.TestCase):
    def test_pure_noise_overfits_about_half_the_time(self):
        # One draw varies widely (its splits share data); the average over draws is 0.5.
        estimates = [
            probability_of_backtest_overfitting(
                [noise(1000 * draw + seed, 400) for seed in range(30)], blocks=8
            )
            for draw in range(20)
        ]
        self.assertEqual(estimates[0].splits, math.comb(8, 4))
        average = sum(e.probability for e in estimates) / len(estimates)
        self.assertGreater(average, 0.35)
        self.assertLess(average, 0.65)

    def test_one_real_edge_is_rarely_overfit(self):
        candidates = [noise(seed, 400) for seed in range(20)]
        candidates.append(noise(99, 400, 0.004))
        estimate = probability_of_backtest_overfitting(candidates, blocks=8)
        self.assertLess(estimate.probability, 0.05)
        self.assertTrue(all(value > 0 for value in estimate.logits))

    def test_invalid_shapes_are_rejected(self):
        with self.assertRaises(ValueError):
            probability_of_backtest_overfitting([noise(1, 40)])
        with self.assertRaises(ValueError):
            probability_of_backtest_overfitting([noise(1, 40), noise(2, 39)])
        with self.assertRaises(ValueError):
            probability_of_backtest_overfitting([noise(1, 40), noise(2, 40)], blocks=5)


class SplitTests(unittest.TestCase):
    def test_walk_forward_tests_after_training(self):
        splits = walk_forward_splits(100, 40, 20)
        self.assertEqual(splits, [((0, 40), (40, 60)), ((20, 60), (60, 80)), ((40, 80), (80, 100))])
        anchored = walk_forward_splits(100, 40, 20, anchored=True, gap=5, step=30)
        self.assertEqual(anchored, [((0, 40), (45, 65)), ((0, 70), (75, 95))])
        for (_, train_stop), (test_start, _) in anchored:
            self.assertLessEqual(train_stop, test_start)

    def test_purged_folds_drop_overlapping_and_embargoed_rows(self):
        splits = purged_k_fold_splits(20, 4, label_horizon=2, embargo=1)
        training, test = splits[1]
        self.assertEqual(test, (5, 10))
        self.assertEqual(training, [0, 1, 2] + list(range(11, 20)))
        for training, (start, stop) in splits:
            self.assertFalse(set(training) & set(range(start, stop)))

    def test_invalid_splits_are_rejected(self):
        with self.assertRaises(ValueError):
            walk_forward_splits(10, 0, 5)
        with self.assertRaises(ValueError):
            purged_k_fold_splits(10, 1)


class RecordingSplitTests(unittest.TestCase):
    def test_windows_never_test_before_they_train(self):
        import csv
        import datetime
        from pathlib import Path
        from tempfile import TemporaryDirectory

        from sim_platform import ContractError
        from sim_platform.research.walk_forward import split_recording

        with TemporaryDirectory() as temp:
            source = Path(temp) / 'bars.csv'
            with source.open('w', newline='') as stream:
                out = csv.writer(stream)
                out.writerow(['Date', 'Open', 'High', 'Low', 'Close', 'Volume'])
                day = datetime.date(2020, 1, 1)
                for i in range(30):
                    out.writerow(
                        [(day + datetime.timedelta(days=i)).isoformat(), 10, 11, 9, 10, 100]
                    )
            windows = split_recording(
                source, Path(temp) / 'w', train_days=10, test_days=5, gap_days=1
            )
            self.assertEqual(len(windows), 3)
            for window in windows:
                self.assertLess(window['train_dates'][1], window['test_dates'][0])
                with open(window['test']) as stream:
                    self.assertEqual(len(stream.read().strip().split('\n')), 6)
            self.assertEqual(windows[0]['test_dates'], ['2020-01-12', '2020-01-16'])
            with self.assertRaises(ContractError):
                split_recording(source, Path(temp) / 'w', train_days=25, test_days=10)


if __name__ == '__main__':
    unittest.main()
