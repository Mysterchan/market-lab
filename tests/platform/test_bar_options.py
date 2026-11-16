"""Price-bar import options, dated custom starts and replay_run() on prepared data."""

import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from sim_platform import ContractError, Platform
from sim_platform.market.models import DAY_NS
from sim_platform.market.prepared.bars import DAILY_OPEN_NS
from sim_platform.market.prepared.builder import build
from sim_platform.market.prepared.reader import PreparedMarket

SECOND = 1_000_000_000
DAILY = """date,open,high,low,close,volume
2026-01-05,100.00,101.00,99.00,100.50,10000
2026-01-06,101.00,102.00,100.00,101.50,10000
2026-01-07,102.00,103.00,101.00,102.50,10000
2026-01-08,103.00,104.00,102.00,103.50,10000
"""
BUY_THEN_SELL = '''
def decide(bars, account, context):
    return dict(target_position=10 if len(bars) == 1 else 0) if bars else None
'''


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix='bar-options-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.csv = self.root / 'daily.csv'
        self.csv.write_text(DAILY)
        self.app = Platform(self.root / 'app')
        self.addCleanup(self.app.close)
        self.profile = self.app.create_profile('Quant')
        self.account = self.app.create_account(self.profile, 'Cash', 100_000_000)

    def run_to_end(self, experiment):
        self.addCleanup(experiment.close)
        while experiment.runner.active:
            experiment.step()
        return experiment


class BookOptionTests(Fixture):
    def test_chosen_spread_and_depth_shape_the_book_and_are_recorded(self):
        out = self.root / 'artifact'
        build(self.csv, out, depth=3, bar_spread=500)
        with PreparedMarket(out) as market:
            book = market.manifest['calendar']['synthetic_book']
            self.assertEqual((book['spread'], book['depth']), (500, 3))
            first = market.snapshot_prefix(1)
            # Half of five cents, rounded down to a cent, below the price; the ask five above.
            self.assertEqual(
                [p for p, _ in first.bids], [1_000_000 - 200 - 100 * i for i in range(3)]
            )
            self.assertEqual([p for p, _ in first.asks], [1_000_300 + 100 * i for i in range(3)])

    def test_invalid_choices_and_a_spread_for_an_itch_file_are_refused(self):
        for spread, depth in ((0, 5), (150, 5), (100_100, 5), (200, 0), (200, 101), ('2', 5)):
            with self.subTest(spread=spread, depth=depth), self.assertRaises(ContractError):
                build(self.csv, self.root / 'bad', depth=depth, bar_spread=spread)
        itch = self.root / 'feed.bin'
        itch.write_bytes(b'\0\0')
        with self.assertRaisesRegex(ContractError, 'price-bar'):
            build(itch, self.root / 'itch', bar_spread=200)

    def test_an_experiment_uses_and_records_the_book_and_another_book_is_prepared_apart(self):
        wide = self.app.open_experiment(
            self.profile,
            self.account,
            source=self.csv,
            execution_mode='controlled',
            book=dict(spread=1000, depth=2),
        )
        self.addCleanup(wide.close)
        view = wide.view()
        self.assertEqual(view['source']['book'], dict(spread=1000, depth=2))
        book = view['source']['prepared']['calendar']['synthetic_book']
        self.assertEqual((book['spread'], book['depth']), (1000, 2))
        market = view['market']
        self.assertEqual(market['asks'][0][0] - market['bids'][0][0], 1000)
        self.assertEqual(len(market['asks']), 2)
        wide.close()
        other = self.app.create_account(self.profile, 'Other', 100_000_000)
        default = self.app.open_experiment(
            self.profile, other, source=self.csv, execution_mode='controlled'
        )
        self.addCleanup(default.close)
        view = default.view()
        self.assertNotIn('book', view['source'])
        self.assertEqual(view['market']['asks'][0][0] - view['market']['bids'][0][0], 200)

    def test_the_library_saves_a_csv_files_book_and_refuses_one_for_itch(self):
        library = self.app.open_datasets()
        self.addCleanup(library.close)
        catalog = library.add(self.csv, book=dict(spread=300))
        entry = next(f for f in catalog['files'] if f['path'] == str(self.csv.resolve()))
        self.assertEqual(entry['book'], dict(spread=300, depth=5))
        catalog = library.add(self.csv, book=dict(spread=300, depth=8))
        entry = next(f for f in catalog['files'] if f['path'] == str(self.csv.resolve()))
        self.assertEqual(entry['book'], dict(spread=300, depth=8))
        itch = self.root / 'feed.bin'
        itch.write_bytes(b'\0\0')
        with self.assertRaises(ValueError):
            library.add(itch, book=dict(spread=300))

    def test_itch_inventory_options_keep_their_earlier_form(self):
        # Artifacts prepared before spread was selectable must still be found and reused.
        from support.trading import fixture

        source = fixture(self.root / 'feed.bin')
        experiment = self.app.open_experiment(
            self.profile, self.account, source=source, execution_mode='controlled'
        )
        self.addCleanup(experiment.close)
        inventory = json.loads((self.root / 'app/prepared-market/inventory.json').read_text())
        self.assertEqual(
            set(inventory['entries'][0]['options']),
            {'symbol', 'depth', 'allow_implicit_eof', 'native_path', 'native_identity'},
        )


class DatedStartTests(Fixture):
    def open(self, **settings):
        return self.app.open_experiment(
            self.profile,
            self.account,
            source=self.csv,
            execution_mode='controlled',
            settings=dict(start_mode='custom', **settings),
        )

    def test_a_custom_start_on_a_later_date(self):
        experiment = self.open(start_date='2026-01-07', start_time_ns=DAILY_OPEN_NS)
        self.addCleanup(experiment.close)
        self.assertEqual(experiment.view()['market']['time_ns'], 2 * DAY_NS + DAILY_OPEN_NS)
        self.assertEqual(experiment.view()['source']['start_date'], '2026-01-07')

    def test_bad_dates_are_refused(self):
        for settings in (
            dict(start_date='2026-01-04', start_time_ns=DAILY_OPEN_NS),
            dict(start_date='07/01/2026', start_time_ns=DAILY_OPEN_NS),
            dict(start_date='2026-01-06', start_time_ns=DAY_NS + 1),
            dict(start_date='2026-01-20', start_time_ns=DAILY_OPEN_NS),
        ):
            with self.subTest(settings=settings), self.assertRaises(ContractError):
                self.open(**settings)
        with self.assertRaisesRegex(ContractError, 'custom'):
            self.app.open_experiment(
                self.profile,
                self.account,
                source=self.csv,
                execution_mode='controlled',
                settings=dict(start_date='2026-01-06'),
            )
        from support.trading import fixture

        with self.assertRaisesRegex(ContractError, 'price-bar'):
            self.app.open_experiment(
                self.profile,
                self.account,
                source=fixture(self.root / 'feed.bin'),
                execution_mode='controlled',
                settings=dict(start_mode='custom', start_date='2026-01-06', start_time_ns=1),
            )


class PreparedReplayRunTests(Fixture):
    def verify(self, run_id):
        self.app.close()
        return Platform.replay_run(self.root / 'app', run_id, self.root / 'replay')

    def test_a_manual_run_on_a_bar_file_with_a_chosen_book_verifies(self):
        experiment = self.app.open_experiment(
            self.profile,
            self.account,
            source=self.csv,
            execution_mode='controlled',
            book=dict(spread=400, depth=2),
            settings=dict(start_mode='custom', start_date='2026-01-06', start_time_ns=0),
        )
        self.addCleanup(experiment.close)
        experiment.step()
        ask = experiment.view()['market']['asks'][0][0]
        experiment.order('buy', 'buy', 10, ask)
        while experiment.view()['market']['time_ns'] < 3 * DAY_NS:
            experiment.step()
        experiment.order('sell', 'sell', 10, experiment.view()['market']['bids'][0][0])
        while experiment.runner.active:
            experiment.step()
        run_id = experiment.run_id
        fills = [e for e in self.app.run_events(self.profile, run_id) if e['kind'] == 'fill']
        self.assertEqual(len(fills), 2)
        experiment.close()
        result = self.verify(run_id)
        self.assertTrue(result['verified'])
        self.assertEqual(result['fills'], 2)

    def test_a_model_run_verifies_without_running_the_model(self):
        model = self.root / 'model.py'
        model.write_text(BUY_THEN_SELL)
        experiment = self.run_to_end(
            self.app.open_experiment(
                self.profile,
                self.account,
                source=self.csv,
                method='model',
                execution_mode='controlled',
                settings=dict(model_file=str(model)),
            )
        )
        run_id = experiment.run_id
        experiment.close()
        model.write_text('raise SystemExit("Replay must not run the model")\n')
        result = self.verify(run_id)
        self.assertTrue(result['verified'])
        self.assertEqual((result['decisions'], result['fills']), (4, 2))

    def test_an_itch_rule_run_verifies_and_a_changed_source_is_refused(self):
        from support.trading import fixture

        source = fixture(self.root / 'feed.bin')
        experiment = self.run_to_end(
            self.app.open_experiment(
                self.profile,
                self.account,
                source=source,
                method='rule',
                execution_mode='controlled',
            )
        )
        run_id = experiment.run_id
        experiment.close()
        self.app.close()
        self.assertTrue(
            Platform.replay_run(self.root / 'app', run_id, self.root / 'first')['verified']
        )
        stat = source.stat()
        # 10 ms later: Windows file times have 100 ns resolution, so 1 ns would not show.
        os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10_000_000))
        with self.assertRaisesRegex(ContractError, 'changed'):
            Platform.replay_run(self.root / 'app', run_id, self.root / 'second')


if __name__ == '__main__':
    unittest.main()
