"""The model decision method: a user's Python file trading in Controlled mode."""

from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from sim_platform import ContractError, Platform
from sim_platform.decisions.policy.model_file import (
    TARGET_ORDER_RULE,
    model_actions,
    money,
)
from sim_platform.decisions.policy.models import Observation
from sim_platform.domain.models import AccountSnapshot, OrderSnapshot, Position
from sim_platform.execution.models import ExecutionConfig, ExecutionSnapshot
from sim_platform.market.models import DAY_NS, MarketSnapshot, calendar_date

# Five trading days; a Monday-to-Friday week after New Year.
DAILY = """date,open,high,low,close,volume
2026-01-05,100.00,101.00,99.00,100.50,10000
2026-01-06,101.00,102.00,100.00,101.50,10000
2026-01-07,102.00,103.00,101.00,102.50,10000
2026-01-08,103.00,104.00,102.00,103.50,10000
2026-01-09,104.00,105.00,103.00,104.50,10000
"""

# Holds 10 shares from the third decision and none from the fifth. It records what
# it was given in a file next to itself, so the test can see the model's inputs.
SCRIPTED = '''
import json, os
CALLS = []
def setup(parameters):
    CALLS.append(dict(setup=parameters))

def decide(bars, account, context):
    CALLS.append(dict(bars=bars, account=account, context=context))
    with open(os.path.join(os.path.dirname(__file__), 'calls.json'), 'w') as stream:
        json.dump(CALLS, stream)
    number = len(CALLS) - 1
    if number in (3, 4):
        return dict(target_position=10)
    if number == 5:
        return dict(target_position=0)
    return None
'''


class ModelExperimentTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix='model-policy-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = Platform(self.root / 'app')
        self.addCleanup(self.app.close)
        self.profile = self.app.create_profile('Quant')
        self.account = self.app.create_account(self.profile, 'Cash', 100_000_000)

    def model(self, text, name='model.py'):
        path = self.root / name
        path.write_text(textwrap.dedent(text))
        return path

    def run_to_end(self, source, model, **settings):
        experiment = self.app.open_experiment(
            self.profile,
            self.account,
            source=source,
            method='model',
            execution_mode='controlled',
            settings=dict(model_file=str(model), **settings),
        )
        self.addCleanup(experiment.close)
        while experiment.runner.active:
            experiment.step()
        return experiment

    def calls(self):
        import json

        return json.loads((self.root / 'calls.json').read_text())

    def test_daily_model_decides_once_per_day_on_completed_bars_and_trades_next_open(self):
        source = self.root / 'daily.csv'
        source.write_text(DAILY)
        experiment = self.run_to_end(
            source, self.model(SCRIPTED), model_parameters=dict(alpha=1), lookback_bars=3
        )
        calls = self.calls()
        self.assertEqual(calls[0], dict(setup=dict(alpha=1)))
        decisions = calls[1:]
        # One call per trading day, at its first event, never once per book step.
        self.assertEqual(
            [c['context']['date'] for c in decisions],
            ['2026-01-05', '2026-01-06', '2026-01-07', '2026-01-08', '2026-01-09'],
        )
        # Only completed bars, oldest first, never today's: no look-ahead.
        self.assertEqual(decisions[0]['bars'], [])
        last = decisions[-1]['bars']
        self.assertEqual([b['date'] for b in last], ['2026-01-06', '2026-01-07', '2026-01-08'])
        self.assertEqual(
            {k: last[-1][k] for k in ('open', 'high', 'low', 'close', 'volume')},
            dict(open=103.0, high=104.0, low=102.0, close=103.5, volume=10000),
        )
        self.assertEqual(decisions[2]['context']['bar'], '1d')
        # Bought at the ask shown at the open of the 7th; sold at the bid on the 9th.
        events = self.app.run_events(self.profile, experiment.run_id)
        fills = [e for e in events if e['kind'] == 'fill']
        self.assertEqual(
            [calendar_date('2026-01-05', e['time_ns']) for e in fills],
            ['2026-01-07', '2026-01-09'],
        )
        ask = int(decisions[2]['context']['ask'] * 10_000)
        bid = int(decisions[4]['context']['bid'] * 10_000)
        self.assertEqual(sum(f['data']['quantity'] for f in fills), 20)
        self.assertLessEqual(fills[0]['data']['price'], ask)
        self.assertGreaterEqual(fills[-1]['data']['price'], bid)
        self.assertEqual(decisions[3]['account']['position'], 10)
        snapshot = self.app.snapshot(self.profile, self.account)
        self.assertEqual(snapshot.positions, ())
        run = self.app.run(self.profile, experiment.run_id, include_records=False)
        parameters = run['config']['decision']['policy']['parameters']
        self.assertEqual(parameters['target_order_rule'], TARGET_ORDER_RULE)
        self.assertEqual((parameters['bar'], parameters['lookback_bars']), ('1d', 3))
        self.assertEqual(len(parameters['model_sha256']), 64)

    def test_model_errors_fail_the_run_with_the_model_message(self):
        source = self.root / 'daily.csv'
        source.write_text(DAILY)
        broken = self.model(
            '''
            def decide(bars, account, context):
                raise ValueError('weights missing')
            '''
        )
        experiment = self.run_to_end(source, broken)
        run = self.app.run(self.profile, experiment.run_id)
        self.assertEqual(run['status'], 'failed')
        self.assertIn('weights missing', str(run))

    def test_a_slow_model_times_out_and_bad_settings_are_refused(self):
        source = self.root / 'daily.csv'
        source.write_text(DAILY)
        slow = self.model(
            '''
            import time
            def decide(bars, account, context):
                time.sleep(5)
            '''
        )
        experiment = self.run_to_end(source, slow, model_timeout_seconds=0.2)
        self.assertIn('did not answer', str(self.app.run(self.profile, experiment.run_id)))
        for settings in (
            dict(model_file=str(slow), lookback_bars=0),
            dict(model_file='relative.py'),
            dict(quantity=5, model_parameters={}),
        ):
            with self.subTest(settings=settings), self.assertRaises(ContractError):
                self.app.open_experiment(
                    self.profile,
                    self.account,
                    source=source,
                    method='model' if 'model_file' in settings else 'rule',
                    execution_mode='controlled',
                    settings=settings,
                )
        with self.assertRaisesRegex(ContractError, 'Controlled mode only'):
            self.app.open_experiment(
                self.profile,
                self.account,
                source=source,
                method='model',
                execution_mode='live',
                settings=dict(model_file=str(slow)),
            )

    def test_the_shipped_example_trades_a_multi_day_intraday_file(self):
        rows = ['timestamp,open,high,low,close,volume']
        for day in ('2026-03-02', '2026-03-03'):
            for minute in range(8):
                price = 100 + minute * 0.5
                rows.append(f'{day} 09:{30 + minute}:00,{price},{price},{price},{price},1000')
        source = self.root / 'minutes.csv'
        source.write_text('\n'.join(rows) + '\n')
        example = ROOT / 'examples' / 'models' / 'moving_average.py'
        experiment = self.run_to_end(
            source, example, model_parameters=dict(window=2, shares=5), lookback_bars=10
        )
        events = self.app.run_events(self.profile, experiment.run_id)
        fills = [e for e in events if e['kind'] == 'fill']
        self.assertTrue(fills)
        self.assertEqual({e['time_ns'] // DAY_NS for e in fills} <= {0, 1}, True)
        self.assertEqual(self.app.run(self.profile, experiment.run_id)['status'], 'completed')


def observation(*, held=0, orders=(), bids=((999_900, 10),), asks=((1_000_100, 10),)):
    account = AccountSnapshot(
        'a',
        'p',
        'USD',
        10_000_000,
        (Position('AAPL', held, held * 1_000_000),) if held else (),
        0,
        0,
        1,
        tuple(orders),
    )
    execution = ExecutionSnapshot(
        'r', 'running', 0, 1, account, 0, 10_000_000, (), ExecutionConfig()
    )
    market = MarketSnapshot('AAPL', 0, 1, 'playing', 'ok', bids, asks)
    return Observation(7, 'market', market, execution, (), ())


def order(client, status='accepted'):
    return OrderSnapshot('o-' + client, 'r:intent:' + client, 'r', 'AAPL', 'buy', 5, 1, 0, status)


class TargetRuleTests(unittest.TestCase):
    def test_target_cancels_own_open_orders_then_trades_the_difference_at_the_touch(self):
        orders = (order('model-3'), order('model-4', 'filled'), order('manual-1'))
        actions = model_actions(dict(target_position=15), observation(held=5, orders=orders))
        self.assertEqual(actions[0], dict(kind='cancel', client_id='model-3'))
        self.assertEqual(len(actions), 2)
        intent = actions[1]['intent']
        self.assertEqual(
            (intent['side'], intent['quantity'], intent['limit_price']), ('buy', 10, 1_000_100)
        )
        sell = model_actions(dict(target_position=2), observation(held=5))[0]['intent']
        self.assertEqual(
            (sell['side'], sell['quantity'], sell['limit_price']), ('sell', 3, 999_900)
        )
        self.assertEqual(model_actions(dict(target_position=5), observation(held=5)), [])
        self.assertEqual(model_actions(dict(target_position=5), observation(asks=())), [])
        self.assertEqual(model_actions(None, observation()), [])

    def test_explicit_orders_round_prices_half_even_and_bad_answers_are_refused(self):
        self.assertEqual(money('100.00005', 'p'), 1_000_000)
        self.assertEqual(money(100.00015, 'p'), 1_000_002)
        actions = model_actions(
            dict(
                orders=[dict(side='buy', quantity=3.0, limit_price='99.5')],
                cancel_open_orders=True,
            ),
            observation(orders=(order('model-1'),)),
        )
        self.assertEqual(actions[0]['kind'], 'cancel')
        self.assertEqual(actions[1]['intent']['limit_price'], 995_000)
        self.assertEqual(actions[1]['intent']['client_id'], 'model-7-0')
        for answer in (
            dict(target_position=-1),
            dict(target_position=1.5),
            dict(target_position=1, orders=[]),
            dict(orders=[dict(side='buy', quantity=1)]),
            dict(orders=[dict(side='hold', quantity=1, limit_price=1)]),
            dict(orders=[dict(side='buy', quantity=1, limit_price=float('nan'))]),
            dict(buy=True),
            [1],
        ):
            with self.subTest(answer=answer), self.assertRaises(ContractError):
                model_actions(answer, observation())


def itch_minutes(path):
    """A one-day ITCH file with a two-sided book and trades over five minutes."""
    from support.prepared_market import add, directory, trade

    minute = 60_000_000_000
    start = 34_200_000_000_000
    data = directory('AAPL', timestamp=start)
    data += add(1, 'B', 1000, 999_900, 'AAPL', timestamp=start + 1)
    data += add(2, 'S', 1000, 1_000_100, 'AAPL', timestamp=start + 2)
    for number in range(5):
        stamp = start + number * minute + 10
        data += trade(number + 1, 1_000_000 + number * 100, 10, stamp)
        data += add(10 + number, 'S', 50, 1_000_100, 'AAPL', timestamp=stamp + 1)
    path.write_bytes(data + b'\0\0')
    return path


class ModelOnItchTests(unittest.TestCase):
    setUp = ModelExperimentTests.setUp
    model = ModelExperimentTests.model

    def test_an_itch_recording_gives_one_minute_bars_and_a_decision_each_minute(self):
        source = itch_minutes(self.root / 'minutes.bin')
        model = self.model(
            '''
            def decide(bars, account, context):
                assert context['bar'] == '60s' and context['date'] is None
                return dict(target_position=10) if len(bars) >= 2 else None
            '''
        )
        experiment = self.app.open_experiment(
            self.profile,
            self.account,
            source=source,
            method='model',
            execution_mode='controlled',
            settings=dict(model_file=str(model), start_mode='beginning'),
        )
        self.addCleanup(experiment.close)
        while experiment.runner.active:
            experiment.step()
        run_id = experiment.run_id
        fills = [e for e in self.app.run_events(self.profile, run_id) if e['kind'] == 'fill']
        self.assertEqual(sum(f['data']['quantity'] for f in fills), 10)
        # The buy follows the second completed minute: decided in the third minute.
        self.assertGreaterEqual(fills[0]['time_ns'], 34_200_000_000_000 + 2 * 60_000_000_000)
        self.assertEqual(self.app.run(self.profile, run_id)['status'], 'completed')

    def test_replay_rebuilds_the_bar_settings_from_the_recorded_policy(self):
        from sim_platform.decisions.policy.model_file import BarSpec
        from sim_platform.replay.runner import RecordedAdapter

        for calendar in (None, dict(timeframe='1d'), dict(timeframe='300s', bar_seconds=300)):
            original = BarSpec(calendar, 7)
            descriptor = dict(
                kind='model',
                name='model-file',
                version='1',
                requires=['book', 'account', 'bars'],
                parameters=dict(bar=original.label, lookback_bars=7),
            )
            rebuilt = RecordedAdapter(descriptor).bar_spec
            self.assertEqual(
                (rebuilt.label, rebuilt.span_ns, rebuilt.minutes, rebuilt.lookback),
                (original.label, original.span_ns, original.minutes, original.lookback),
            )


class UnchangedPolicyTests(unittest.TestCase):
    def test_observations_of_existing_policies_have_no_bars_field(self):
        # Recorded fingerprints of earlier runs hash this JSON; it must not change.
        self.assertNotIn('bars', observation().to_json())


if __name__ == '__main__':
    unittest.main()


def signal_wheel(folder):
    """An offline wheel providing module signal_lib with SHARES = 7."""
    import zipfile

    dist = 'signal_lib-1.0.dist-info'
    files = {
        'signal_lib.py': 'SHARES = 7\n',
        dist + '/METADATA': 'Metadata-Version: 2.1\nName: signal_lib\nVersion: 1.0\n',
        dist + '/WHEEL': 'Wheel-Version: 1.0\nGenerator: fixture\nRoot-Is-Purelib: true\n'
        'Tag: py3-none-any\n',
    }
    files[dist + '/RECORD'] = ''.join(name + ',,\n' for name in [*files, dist + '/RECORD'])
    folder.mkdir()
    with zipfile.ZipFile(folder / 'signal_lib-1.0-py3-none-any.whl', 'w') as archive:
        for name, text in files.items():
            archive.writestr(name, text)
    return folder


class ModelEnvironmentTests(unittest.TestCase):
    setUp = ModelExperimentTests.setUp
    run_to_end = ModelExperimentTests.run_to_end

    def test_a_model_imports_a_package_from_its_prepared_environment(self):
        import json

        project = self.root / 'lstm'
        project.mkdir()
        version = f'{sys.version_info.major}.{sys.version_info.minor}'
        (project / 'model.json').write_text(json.dumps(dict(python=version)))
        (project / 'requirements.txt').write_text('signal_lib==1.0\n')
        (project / 'model.py').write_text(
            'import signal_lib\n'
            'def decide(bars, account, context):\n'
            '    return dict(target_position=signal_lib.SHARES) if bars else None\n'
        )
        self.assertIsNone(self.app.model_environment(project))
        job = self.app.prepare_model_environment(project, wheelhouse=signal_wheel(self.root / 'w'))
        self.addCleanup(job.close)
        self.assertEqual(job.wait(60)['state'], 'ready', job.snapshot())
        python = self.app.model_environment(project)
        self.assertTrue(Path(python).is_file())
        source = self.root / 'daily.csv'
        source.write_text(DAILY)
        self.run_to_end(source, project / 'model.py', model_python=python)
        position = self.app.snapshot(self.profile, self.account).positions
        self.assertEqual([p.quantity for p in position], [7])
        # The App's own interpreter does not have the package, so loading the model fails.
        self.account = self.app.create_account(self.profile, 'Other', 100_000_000)
        failed = self.run_to_end(source, project / 'model.py')
        run = self.app.run(self.profile, failed.run_id)
        self.assertEqual(run['status'], 'failed')
        self.assertIn('signal_lib', str(run))
