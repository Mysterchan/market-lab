"""Agent evaluation: statistics, comparability, memorisation flags, disguise and suites."""

import csv
import datetime
import math
import sys
import time
from pathlib import Path
from random import Random
from tempfile import TemporaryDirectory
import unittest

from sim_platform import ContractError, Platform
from sim_platform.market.prepared.bars import read_bars
from sim_platform.market.prepared.disguise import disguise_price_bars
from sim_platform.research.evaluation import (
    agent_model_usage,
    bootstrap_mean_interval,
    check_comparable,
    compare_with_baseline,
    entry_returns,
    selection_statistics,
    holm_adjust,
    memorisation_flags,
    model_cost,
    permutation_p_value,
    sign_flip_p_value,
)
from sim_platform.research.metrics import sharpe_ratio, sortino_ratio

DAY = datetime.date(2020, 1, 6)  # a Monday


def write_bars(path, days, phase=0.0, extra=False):
    with path.open('w', newline='') as stream:
        out = csv.writer(stream)
        out.writerow(
            ['Date', 'Open', 'High', 'Low', 'Close', 'Volume'] + (['Adj Close'] if extra else [])
        )
        for i in range(days):
            price = 100 + 5 * math.sin(i / 2 + phase) + i * 0.1
            row = [
                (DAY + datetime.timedelta(days=i)).isoformat(),
                f'{price:.2f}',
                f'{price + 1:.2f}',
                f'{price - 1:.2f}',
                f'{price + 0.5:.2f}',
                100000,
            ]
            out.writerow(row + ([f'{price:.4f}'] if extra else []))
    return path


def fake_report(run_id, *, agent=False, speed='1x', start_date='2020-01-06', last_day=4, **config):
    source = dict(
        path='/data/x.csv',
        bytes=10,
        mtime_ns=1,
        start_origin=dict(sequence=0, time_ns=0),
        prepared=dict(calendar=dict(start_date=start_date) if start_date else {}, depth=5),
    )
    run_config = dict(
        research_source=source,
        execution=dict(fee_per_share=100, latency_ns=0),
        **config,
    )
    if agent:
        run_config['agent'] = dict(end_ns='100', speed=speed)
    return dict(
        run=dict(
            id=run_id,
            universe=['AAPL'],
            config=run_config,
            initial_snapshot=dict(cash=1000, positions=[]),
        ),
        market_data=dict(daily_closes=[[last_day, 0, 100]]),
    )


def temporary_directory():
    """A temporary folder; on Windows its removal tolerates a file still open.

    Known open item: on Windows, the workspace database of a batch suite can still be
    open for a moment after Platform.close(), which only fails this cleanup step.
    """
    if sys.platform == 'win32':
        return TemporaryDirectory(ignore_cleanup_errors=True)
    return TemporaryDirectory()


class StatisticsTests(unittest.TestCase):
    def test_permutation_test_is_exact_for_small_groups(self):
        p, exact = permutation_p_value([6, 7, 8, 9, 10], [1, 2, 3, 4, 5], Random(1), 10_000)
        self.assertTrue(exact)
        # Only the observed split and its mirror are as extreme: 2 of C(10, 5) = 252.
        self.assertAlmostEqual(p, 2 / 252)

    def test_sign_flip_against_a_constant(self):
        p, exact = sign_flip_p_value([0.1, 0.1, 0.1], Random(1), 10_000)
        self.assertTrue(exact)
        self.assertAlmostEqual(p, 2 / 8)
        comparison = compare_with_baseline([3.0, 2.0, 4.0], [1.0], Random(1), 1000)
        self.assertEqual(comparison['test'], 'sign-flip against a constant baseline')
        self.assertEqual(comparison['difference'], 2.0)
        self.assertTrue(comparison['insufficient'])

    def test_two_constant_groups_have_an_exact_difference_and_no_test(self):
        comparison = compare_with_baseline([3.0, 3.0, 3.0], [1.0, 1.0], Random(1), 1000)
        self.assertEqual(comparison['difference'], 2.0)
        self.assertEqual(comparison['interval'], [2.0, 2.0])
        self.assertIsNone(comparison['p_value'])
        self.assertTrue(comparison['deterministic'])
        self.assertFalse(comparison['insufficient'])

    def test_entry_returns_average_runs_of_the_common_length(self):
        self.assertEqual(entry_returns([[0.1, 0.3], [0.3, 0.1], [0.5], None]), [0.2, 0.2])
        self.assertIsNone(entry_returns([None, []]))

    def test_selection_deflates_for_trials_and_estimates_pbo(self):
        rng = Random(5)
        series = {f'E{i}': [rng.gauss(0.001, 0.01) for _ in range(64)] for i in range(4)}
        one, overfitting = selection_statistics(series, {})
        many, _ = selection_statistics(series, {'E0': 500})
        self.assertEqual(one['E0']['deflated_sharpe'], one['E0']['probabilistic_sharpe'])
        self.assertLess(many['E0']['deflated_sharpe'], one['E0']['deflated_sharpe'])
        self.assertEqual(overfitting['candidates'], 4)
        self.assertEqual(overfitting['blocks'], 8)
        self.assertTrue(0 <= overfitting['probability'] <= 1)
        _, none = selection_statistics({'A': series['E0'], 'B': None}, {})
        self.assertIsNone(none)

    def test_holm_adjustment(self):
        self.assertEqual([round(p, 6) for p in holm_adjust([0.01, 0.04, 0.03])], [0.03, 0.06, 0.06])
        self.assertEqual(holm_adjust([None, 0.02]), [None, 0.02])

    def test_bootstrap_is_seeded_and_brackets_the_mean(self):
        values = [0.01, -0.02, 0.03, 0.015, -0.005, 0.02]
        first = bootstrap_mean_interval(values, Random(7), 2000)
        self.assertEqual(first, bootstrap_mean_interval(values, Random(7), 2000))
        self.assertLess(first[0], sum(values) / len(values))
        self.assertGreater(first[1], sum(values) / len(values))

    def test_sortino_counts_only_losses(self):
        returns = [0.01, -0.02, 0.03, -0.01]
        downside = ((0.02**2 + 0.01**2) / 4) ** 0.5
        self.assertAlmostEqual(sortino_ratio(returns), 0.0025 / downside * 252**0.5)
        self.assertIsNone(sortino_ratio([0.01, 0.02]))
        self.assertIsNotNone(sharpe_ratio(returns))


class ModelUsageTests(unittest.TestCase):
    def test_usage_is_read_from_the_agent_log(self):
        def control(event, **details):
            return dict(kind='control', event=event, details=details)

        archive = dict(
            records=[
                control('attempt_dispatched', attempt_id='1', dispatched_ns='1000000000'),
                control('attempt_retry', error='RATE_LIMIT'),
                control('attempt_dispatched', attempt_id='2', dispatched_ns='3000000000'),
                control(
                    'attempt_completed',
                    attempt_id='2',
                    received_ns='5000000000',
                    result=dict(
                        received=True,
                        usage=dict(
                            input_tokens=50, output_tokens=7, model='m-2026-01-01', label='trader'
                        ),
                    ),
                ),
                dict(kind='tool', event='ignored'),
            ]
        )
        usage = agent_model_usage(archive)
        self.assertEqual(
            usage,
            dict(
                model_calls=2,
                failed_calls=1,
                input_tokens=50,
                output_tokens=7,
                mean_latency_seconds=2.0,
                served_models=['m-2026-01-01'],
                by_label=dict(trader=dict(replies=1, input_tokens=50, output_tokens=7)),
            ),
        )
        self.assertAlmostEqual(model_cost(usage, dict(input=1, output=4)), 78 / 1e6)
        self.assertIsNone(model_cost(usage, None))
        self.assertIsNone(agent_model_usage(None))
        # A reply without usage leaves tokens unknown rather than zero.
        quiet = agent_model_usage(dict(records=[control('attempt_completed', attempt_id='1')]))
        self.assertIsNone(quiet['input_tokens'])

    def test_served_model_is_kept_and_validated(self):
        from sim_platform.experiments.scheduling.protocol import model_usage, valid_usage

        reply = dict(usage=dict(input_tokens=3, output_tokens=1), model='m-2026-01-01')
        usage = model_usage(reply)
        self.assertEqual(usage['model'], 'm-2026-01-01')
        self.assertTrue(valid_usage(usage))
        self.assertFalse(valid_usage(dict(model='x' * 129)))
        self.assertTrue(valid_usage(dict(input_tokens=1, label='news analyst')))
        self.assertFalse(valid_usage(dict(label='x' * 65)))
        self.assertFalse(valid_usage(dict(input_tokens='3')))
        self.assertNotIn('model', model_usage(dict(model='m')))

    def test_several_served_models_are_reported(self):
        from sim_platform.research.evaluation import served_models

        reports = [
            dict(model_usage=dict(served_models=['a'])),
            dict(model_usage=dict(served_models=['b', 'a'])),
            dict(),
        ]
        self.assertEqual(served_models(reports), ['a', 'b'])

    def test_provider_usage_keeps_only_token_counts(self):
        from sim_platform.experiments.scheduling.protocol import model_usage, valid_usage

        reply = dict(
            usage=dict(
                input_tokens=10,
                output_tokens=3,
                total_tokens=13,
                input_tokens_details=dict(cached_tokens=4),
                note='text is dropped',
            )
        )
        counts = model_usage(reply)
        self.assertEqual(
            counts, dict(input_tokens=10, output_tokens=3, total_tokens=13, cached_tokens=4)
        )
        self.assertTrue(valid_usage(counts))
        self.assertFalse(valid_usage(dict(input_tokens=-1)))
        self.assertFalse(valid_usage(dict(prompt='secret')))
        self.assertEqual(model_usage(dict(output=[])), {})


class ComparabilityAndFlagTests(unittest.TestCase):
    def test_different_execution_settings_are_refused(self):
        same = [('A', fake_report('a')), ('B', fake_report('b'))]
        self.assertEqual(check_comparable(same)['starting_cash'], 1000)
        other = fake_report('c')
        other['run']['config']['execution']['fee_per_share'] = 0
        with self.assertRaisesRegex(ContractError, 'execution.fee_per_share differs'):
            check_comparable([('A', fake_report('a')), ('C', other)])

    def test_live_and_turn_based_agent_runs_are_not_mixed(self):
        live = fake_report('l', agent=True)
        turn = fake_report('t', agent=True, speed='turn')
        rule = fake_report('r')
        check_comparable([('Live', live), ('Rule', rule)])
        check_comparable([('Turn', turn), ('Rule', rule)])
        with self.assertRaisesRegex(ContractError, 'Live and turn-based'):
            check_comparable([('Live', live), ('Turn', turn), ('Rule', rule)])

    def test_live_pacing_and_prepared_copy_do_not_matter(self):
        other = fake_report('b')
        other['run']['config']['research_source']['execution_mode'] = 'live'
        other['run']['config']['research_source']['prepared']['artifact'] = '/elsewhere'
        check_comparable([('A', fake_report('a')), ('B', other)])

    def test_memorisation_flags(self):
        self.assertEqual(memorisation_flags(fake_report('r'), None), [])
        agent = fake_report('a', agent=True)
        self.assertEqual(memorisation_flags(agent, None), ['cutoff-unknown'])
        self.assertEqual(memorisation_flags(agent, '2023-10-01'), ['possible-memorisation'])
        self.assertEqual(memorisation_flags(agent, '2019-12-31'), ['after-cutoff'])
        future = fake_report('f', agent=True, start_date='2100-01-04')
        self.assertEqual(memorisation_flags(future, '2023-10-01'), ['disguised'])
        itch = fake_report('i', agent=True, start_date=None)
        self.assertEqual(memorisation_flags(itch, '2023-10-01'), ['dates-unknown'])
        with self.assertRaises(ContractError):
            memorisation_flags(agent, 'October 2023')


class DisguiseTests(unittest.TestCase):
    def test_disguised_copy_moves_dates_scales_prices_and_drops_columns(self):
        with temporary_directory() as temp:
            root = Path(temp)
            source = write_bars(root / 'aapl.csv', 6, extra=True)
            mapping = disguise_price_bars(
                source, root / 'out.csv', start_date='2100-01-04', price_scale=0.5
            )
            self.assertEqual(mapping['first_date'], '2020-01-06')
            self.assertEqual(mapping['disguised_first_date'], '2100-01-04')
            self.assertEqual(mapping['shift_days'] % 7, 0)
            with (root / 'out.csv').open() as stream:
                rows = list(csv.reader(stream))
            self.assertEqual(rows[0], ['date', 'open', 'high', 'low', 'close', 'volume'])
            self.assertEqual(rows[1][0], '2100-01-04')
            self.assertEqual(rows[1][1], '50.00')  # 100.00 × 0.5
            original = read_bars(source)
            disguised = read_bars(root / 'out.csv')
            self.assertEqual(len(original.bars), len(disguised.bars))
            self.assertEqual([b.volume for b in original.bars], [b.volume for b in disguised.bars])

    def test_drawn_scale_is_seeded_and_bounded(self):
        with temporary_directory() as temp:
            root = Path(temp)
            source = write_bars(root / 'x.csv', 3)
            first = disguise_price_bars(source, root / 'a.csv', seed=5)['price_scale']
            again = disguise_price_bars(source, root / 'b.csv', seed=5)['price_scale']
            self.assertEqual(first, again)
            self.assertTrue(0.5 <= first <= 2)
            with self.assertRaises(ContractError):
                disguise_price_bars(source, source)
            with self.assertRaises(ContractError):
                disguise_price_bars(source, root / 'c.csv', price_scale=0)


class SuiteTests(unittest.TestCase):
    """One real suite on short recordings; each run writes every decision, so keep it small."""

    def test_suite_runs_entries_and_platform_evaluates_saved_runs(self):
        with temporary_directory() as temp:
            root = Path(temp)
            app = Platform(root / 'accounts')
            self.addCleanup(app.close)
            first = write_bars(root / 'a.csv', 5)
            mapping = app.disguise_recording(first)
            self.assertTrue(mapping['output'].endswith('.csv'))
            self.assertTrue(Path(mapping['output']).name.startswith('disguised-'))
            profile = app.create_profile('Research')
            with app.open_batch() as batch:
                report = batch.evaluate_suite(
                    [mapping['output']],
                    [
                        dict(name='Hold 1 hour', hold_ns=3600 * 10**9),
                        dict(name='Hold 1 day', hold_ns=86400 * 10**9, repetitions=2),
                    ],
                    baseline='Hold 1 hour',
                    symbol='ASSET1',
                    profile=profile,
                    resamples=200,
                )
                with self.assertRaisesRegex(ContractError, 'Agent entry needs'):
                    batch.evaluate_suite([str(first)], [dict(name='A', policy='agent')])
            self.assertEqual(report['format'], 'agent-evaluation-suite-v1')
            self.assertEqual(report['status'], 'completed')
            evaluation = report['recordings'][0]['evaluation']
            entries = {e['name']: e for e in evaluation['entries']}
            self.assertEqual(len(entries['Hold 1 day']['runs']), 2)
            self.assertIsNone(entries['Hold 1 hour']['comparison'])
            comparison = entries['Hold 1 day']['comparison']
            # A rule repeats exactly: both groups are constant, so no test is made.
            self.assertEqual(comparison['test'], 'none: both constant')
            self.assertIsNone(comparison['p_value_holm'])
            self.assertEqual(evaluation['settings']['universe'], ['ASSET1'])
            figures = entries['Hold 1 day']['runs'][0]['figures']
            for name in ('total_return', 'max_drawdown_ratio', 'exposure', 'turnover'):
                self.assertIsNotNone(figures[name], name)

            ids = {name: [r['run_id'] for r in e['runs']] for name, e in entries.items()}
            again = app.evaluation(profile, ids, baseline='Hold 1 hour', resamples=200)
            self.assertEqual(
                [e['statistics'] for e in again['entries']],
                [e['statistics'] for e in evaluation['entries']],
            )
            with self.assertRaisesRegex(ContractError, 'baseline'):
                app.evaluation(profile, ids, baseline='Missing')
            with self.assertRaisesRegex(ContractError, 'training_cutoffs'):
                app.evaluation(profile, ids, training_cutoffs={'Other': '2023-01-01'})
            searched = app.evaluation(profile, ids, resamples=200, trials={'Hold 1 day': 50})
            rows = {e['name']: e['selection'] for e in searched['entries']}
            self.assertEqual(rows['Hold 1 day']['trials'], 50)
            self.assertEqual(
                searched['trial_sources'], {'Hold 1 hour': 'one', 'Hold 1 day': 'given'}
            )
            self.assertEqual(rows['Hold 1 hour']['trials'], 1)
            self.assertGreater(rows['Hold 1 day']['periods'], 1)
            with self.assertRaisesRegex(ContractError, 'trials'):
                app.evaluation(profile, ids, trials={'Hold 1 day': 0})


class BenchmarkTests(unittest.TestCase):
    def test_recordings_are_reproducible_and_baselines_are_fixed(self):
        from sim_platform.research.benchmark import (
            REGIMES,
            baseline_entries,
            evaluate_benchmark,
            write_benchmark,
        )

        with temporary_directory() as temp:
            root = Path(temp)
            first = write_benchmark(root / 'a', days=30)
            second = write_benchmark(root / 'b', days=30)
            self.assertEqual(set(first['recordings']), set(REGIMES))
            for regime in REGIMES:
                self.assertEqual(
                    Path(first['recordings'][regime]).read_bytes(),
                    Path(second['recordings'][regime]).read_bytes(),
                )
            other = write_benchmark(root / 'c', days=30, seed=7)
            self.assertNotEqual(
                Path(first['recordings']['volatile']).read_bytes(),
                Path(other['recordings']['volatile']).read_bytes(),
            )
            names = [e['name'] for e in baseline_entries(first['model_file'])]
            self.assertEqual(names[0], 'Buy and hold')
            app = Platform(root / 'app')
            self.addCleanup(app.close)
            profile = app.create_profile('Bench')
            with app.open_batch() as batch:
                with self.assertRaisesRegex(ContractError, 'regimes'):
                    evaluate_benchmark(batch, root / 'x', [], regimes=['moon'], profile=profile)
                report = evaluate_benchmark(
                    batch,
                    root / 'bench',
                    [dict(name='Two-day rule', policy='rule', hold_ns=2 * 86400 * 10**9)],
                    profile=profile,
                    resamples=200,
                    regimes=['uptrend'],
                    benchmark_days=12,
                )
            self.assertEqual(report['status'], 'completed')
            self.assertEqual(report['benchmark']['regimes'], ['uptrend'])
            entries = report['recordings'][0]['evaluation']['entries']
            self.assertEqual(len(entries), 4)


class AgentSuiteTests(unittest.TestCase):
    """An Agent and a rule in one suite: same window, comparable runs. Runs in real time."""

    def test_agent_and_rule_share_the_agents_window(self):
        from sim_platform.examples.configured_agent_demo import example_configuration
        from sim_platform.examples.market_demo import write_fixture
        from support.models import BuyThenFinishProvider

        with temporary_directory() as temp:
            root = Path(temp)
            source, _ = write_fixture(root)
            definition, settings = example_configuration()
            grants = ['market.quote', 'control.finish', 'model.request', 'orders.submit_market']
            definition['requires'] = settings['granted_tools'] = grants
            app = Platform(root / 'accounts')
            self.addCleanup(app.close)
            profile = app.create_profile('Research')
            app.create_agent(profile, definition)
            app.set_model_credential('demo-key', 'unused')
            with app.open_batch() as batch:
                report = batch.evaluate_suite(
                    [source],
                    [
                        dict(
                            name='Buyer',
                            policy='agent',
                            agent_id=definition['agent_id'],
                            revision=1,
                            settings=settings,
                            model=dict(
                                provider='openai-responses', model='fake', credential_ref='demo-key'
                            ),
                            repetitions=2,
                        ),
                        dict(name='Rule', policy='rule', hold_ns=10**9),
                    ],
                    baseline='Rule',
                    symbol='AAPL',
                    profile=profile,
                    model_provider=BuyThenFinishProvider,
                    resamples=200,
                    concurrency=2,
                    token_prices=dict(input=2.5, output=10),
                )
                with self.assertRaisesRegex(ContractError, 'concurrency'):
                    batch.evaluate_suite([source], [dict(name='R')], concurrency=0)
                with self.assertRaisesRegex(ContractError, 'profile'):
                    batch.evaluate_suite(
                        [source],
                        [
                            dict(
                                name='A',
                                policy='agent',
                                agent_id='x',
                                revision=1,
                                settings=settings,
                                model=dict(model='m'),
                            )
                        ],
                    )
            evaluation = report['recordings'][0]['evaluation']
            entries = {e['name']: e for e in evaluation['entries']}
            self.assertEqual(len(entries['Buyer']['runs']), 2)
            self.assertEqual(report['concurrency'], 2)
            window = dict(start_ns=int(settings['start_ns']), end_ns=int(settings['end_ns']))
            for entry in entries.values():
                for run in entry['runs']:
                    self.assertEqual(run['window'], window)
            self.assertEqual(entries['Buyer']['runs'][0]['flags'], ['dates-unknown'])
            self.assertEqual(entries['Rule']['runs'][0]['flags'], [])
            self.assertFalse(any('windows' in w for w in evaluation['warnings']))
            # The scripted Agent buys 5 shares in each run and keeps them.
            self.assertEqual(entries['Buyer']['statistics']['exposure']['mean'], 1.0)
            self.assertIsNotNone(entries['Buyer']['comparison'])
            # Two model calls per run (buy, then finish), each reporting 100 + 20 tokens.
            stats = entries['Buyer']['statistics']
            self.assertEqual(stats['model_calls']['mean'], 2)
            self.assertEqual(stats['failed_calls']['mean'], 0)
            self.assertEqual(stats['input_tokens']['mean'], 200)
            self.assertEqual(stats['output_tokens']['mean'], 40)
            self.assertAlmostEqual(stats['model_cost']['mean'], (200 * 2.5 + 40 * 10) / 1e6)
            self.assertGreaterEqual(stats['mean_latency_seconds']['mean'], 0)
            self.assertEqual(entries['Rule']['statistics']['model_calls']['n'], 0)
            self.assertEqual(entries['Buyer']['pacing'], 'live')
            self.assertIsNone(entries['Rule']['pacing'])

    def test_turn_based_suite_and_the_cost_of_thinking_time(self):
        from sim_platform.examples.configured_agent_demo import example_configuration
        from sim_platform.examples.market_demo import write_fixture
        from sim_platform.research.evaluation import latency_cost
        from support.models import SlowBuyThenFinishProvider

        with temporary_directory() as temp:
            root = Path(temp)
            source, _ = write_fixture(root)
            definition, settings = example_configuration()
            grants = ['market.quote', 'control.finish', 'model.request', 'orders.submit_market']
            definition['requires'] = settings['granted_tools'] = grants
            app = Platform(root / 'accounts')
            self.addCleanup(app.close)
            profile = app.create_profile('Research')
            app.create_agent(profile, definition)
            app.set_model_credential('demo-key', 'unused')
            model = dict(provider='openai-responses', model='fake', credential_ref='demo-key')

            def suite(speed):
                entry = dict(
                    name='Buyer',
                    policy='agent',
                    agent_id=definition['agent_id'],
                    revision=1,
                    settings=dict(settings, speed=speed),
                    model=model,
                    repetitions=2,
                )
                with app.open_batch() as batch:
                    report = batch.evaluate_suite(
                        [source],
                        [entry, dict(name='Rule', policy='rule', hold_ns=10**9)],
                        baseline='Rule',
                        symbol='AAPL',
                        profile=profile,
                        model_provider=lambda: SlowBuyThenFinishProvider(0.2),
                        resamples=200,
                        concurrency=2,
                    )
                return report['recordings'][0]['evaluation']

            started = time.monotonic()
            turn = suite('turn')
            turn_seconds = time.monotonic() - started
            live = suite('1x')
            buyer = next(e for e in turn['entries'] if e['name'] == 'Buyer')
            self.assertEqual(buyer['pacing'], 'turn')
            self.assertIsNone(buyer['statistics']['mean_latency_seconds']['mean'])
            self.assertEqual(buyer['statistics']['model_calls']['mean'], 2)
            # Two turn-based runs and a rule take far less than the four-second window
            # (process start-up on Windows runners alone can take seconds).
            self.assertLess(turn_seconds, 10 if sys.platform == 'win32' else 4)
            cost = latency_cost(live, turn, 'Buyer', resamples=200)
            self.assertEqual(cost['runs'], dict(live=2, turn=2))
            self.assertAlmostEqual(cost['cost'], cost['live_mean'] - cost['turn_mean'])
            with self.assertRaisesRegex(ContractError, 'not a live'):
                latency_cost(turn, turn, 'Buyer', resamples=200)


if __name__ == '__main__':
    unittest.main()
