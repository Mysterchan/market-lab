"""Result-page data: archive on natural finish, event text, Sharpe note, Verify run."""

from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from sim_platform import ContractError, Platform
from sim_platform.desktop.presentation import event_text, trade_statistics

DAILY = """date,open,high,low,close,volume
2026-01-05,100.00,101.00,99.00,100.50,10000
2026-01-06,101.00,102.00,100.00,101.50,10000
2026-01-07,99.00,100.00,98.00,98.50,10000
"""


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix='result-page-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'daily.csv').write_text(DAILY)
        self.app = Platform(self.root / 'app')
        self.addCleanup(self.app.close)
        self.profile = self.app.create_profile('Quant')
        self.account = self.app.create_account(self.profile, 'Cash', 100_000_000)

    def finished_run(self):
        experiment = self.app.open_experiment(
            self.profile, self.account, source=self.root / 'daily.csv', execution_mode='controlled'
        )
        self.addCleanup(experiment.close)
        experiment.step()
        experiment.order('b', 'buy', 5, 1_010_000)
        while experiment.runner.active:
            experiment.step()
        return experiment


class ArchiveTests(Fixture):
    def test_a_run_that_finishes_by_stepping_saves_its_market_data_at_once(self):
        experiment = self.finished_run()
        # The experiment is still open: its result must already have bars and closes.
        research = self.app.open_research()
        self.addCleanup(research.close)
        view = research.view_run(self.profile, experiment.run_id)
        self.assertTrue(view['bars'])
        self.assertEqual(len(view['daily_closes']), 3)


class EventTextTests(unittest.TestCase):
    def test_events_get_plain_labels_and_formatted_details(self):
        intent = dict(
            kind='intent',
            data=dict(intent=dict(client_id='b1', side='buy', quantity=5, limit_price=1_000_100)),
        )
        self.assertEqual(
            event_text(intent, {}),
            dict(label='Order sent', details='b1 · Buy 5 shares · limit $100.01'),
        )
        market = dict(intent, data=dict(intent['data'], market_order={}))
        self.assertTrue(event_text(market, {})['details'].endswith('market order'))
        fill = dict(kind='fill', data=dict(order_id='o', quantity=5, price=1_000_100, fee=500))
        self.assertEqual(
            event_text(fill, {'o': 'sell'}),
            dict(label='Fill', details='Sold 5 shares at $100.01 · fee $0.05'),
        )
        rejected = dict(kind='rejected', data=dict(intent_id='r:intent:x', reason='no cash'))
        self.assertEqual(event_text(rejected, {})['details'], 'x · no cash')
        self.assertEqual(
            event_text(dict(kind='execution_tick', data={}), {})['label'], 'Market checkpoint'
        )


class SharpeNoteTests(unittest.TestCase):
    def test_a_sharpe_ratio_from_few_daily_returns_is_marked_unreliable(self):
        rows = [dict(time_ns=1, side='buy', quantity=1, price=100, fee=0)]
        view = dict(run=dict(initial_snapshot=dict(cash=1000, positions=[])))
        for days, reliable in ((4, False), (20, True)):
            closes = [[d, d * 10 + 9, 100 + d % 3] for d in range(days)]
            result = trade_statistics(dict(view, daily_closes=closes), rows)
            self.assertEqual(
                (result['sharpe_returns'], result['sharpe_reliable']), (days, reliable)
            )


class VerifyRunTests(Fixture):
    def test_verify_run_replays_a_finished_controlled_run(self):
        from sim_platform.desktop.tasks import verify_saved_run

        experiment = self.finished_run()
        run_id = experiment.run_id
        experiment.close()
        message = verify_saved_run(self.root / 'app', run_id)
        self.assertTrue(message.startswith('Verified · '), message)
        self.assertIn('1 fills', message)
        with self.assertRaises(ContractError):
            verify_saved_run(self.root / 'app', 'unknown-run')


if __name__ == '__main__':
    unittest.main()
