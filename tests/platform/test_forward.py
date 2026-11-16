"""Forward testing in shadow mode: segments of arriving bars on one simulated account."""

import csv
import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from sim_platform import Platform
from sim_platform.research.forward import CsvFeed, ForwardTest

DAY_NS = 86_400 * 10**9


class GrowingFeed:
    """Stands in for a vendor: releases a few more daily bars each time it is asked."""

    def __init__(self, path):
        self.path = path
        self.released = 0
        self.rows = []
        price = 50.0
        for i in range(8):
            day = datetime.date(2024, 1, 1) + datetime.timedelta(days=i)
            self.rows.append([day.isoformat(), price, price + 1, price - 1, price + 0.5, 100_000])
            price += 0.5

    def release(self, count):
        self.released += count
        with self.path.open('w', newline='') as stream:
            out = csv.writer(stream)
            out.writerow(['Date', 'Open', 'High', 'Low', 'Close', 'Volume'])
            out.writerows(self.rows[: self.released])


class ForwardTests(unittest.TestCase):
    def test_segments_carry_the_account_and_log_each_arrival(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            vendor = GrowingFeed(root / 'vendor.csv')
            with Platform(root / 'app') as app:
                profile = app.create_profile('Forward')
                account = app.create_account(profile, 'Shadow', 100_000_000)
                test = ForwardTest(
                    app,
                    profile,
                    account,
                    CsvFeed(vendor.path),
                    root / 'forward',
                    symbol='ACME',
                    method='rule',
                    settings=dict(quantity=10, hold_ns=100 * DAY_NS),
                )
                vendor.release(4)
                first = test.step()
                self.assertEqual(first['bars'], 4)
                self.assertEqual(first['status'], 'completed')
                self.assertIsNone(test.step())  # nothing new yet
                held = {p.symbol: p.quantity for p in app.snapshot(profile, account).positions}
                self.assertEqual(held.get('ACME'), 10)
                vendor.release(3)
                second = test.step()
                self.assertEqual((second['first_bar'], second['bars']), ('2024-01-05', 3))
                run = app.run(profile, second['run_id'], include_records=False)
                # The second segment starts from the first one's holdings.
                positions = run['initial_snapshot']['positions']
                self.assertEqual(positions[0]['quantity'], 10)
                log = test.segments()
                self.assertEqual([s['run_id'] for s in log], [first['run_id'], second['run_id']])
                self.assertNotEqual(log[0]['recording_sha256'], log[1]['recording_sha256'])
                lines = (root / 'forward' / 'ACME-forward.csv').read_text().splitlines()
                self.assertEqual(len(lines), 1 + 7)
                self.assertTrue(app.verify_run(profile, first['run_id'])['sealed'])

    def test_an_agent_trades_each_segment(self):
        from sim_platform.examples.configured_agent_demo import example_configuration
        from sim_platform.models.contracts import ModelProfile
        from sim_platform.models.providers.rehearsal import RehearsalProvider

        with TemporaryDirectory() as folder:
            root = Path(folder)
            vendor = GrowingFeed(root / 'vendor.csv')
            with Platform(root / 'app') as app:
                profile = app.create_profile('Forward')
                account = app.create_account(profile, 'Shadow', 100_000_000)
                definition, settings = example_configuration()
                grants = ['market.quote', 'control.finish', 'model.request', 'orders.submit_market']
                definition['requires'] = settings['granted_tools'] = grants
                definition['schedule']['interval_seconds'] = '86400'
                app.create_agent(profile, definition)
                app.set_model_credential('key', 'unused')

                def configure(experiment):
                    app.configure_experiment_model(
                        experiment,
                        ModelProfile('openai-responses', 'rehearsal', 'key'),
                        provider=RehearsalProvider('ACME'),
                    )

                test = ForwardTest(
                    app,
                    profile,
                    account,
                    CsvFeed(vendor.path),
                    root / 'forward',
                    symbol='ACME',
                    method='agent',
                    settings=dict(settings, speed='turn'),
                    agent_id=definition['agent_id'],
                    revision=1,
                    configure=configure,
                )
                vendor.release(3)
                first = test.step()
                vendor.release(2)
                second = test.step()
                self.assertEqual([first['status'], second['status']], ['completed', 'completed'])
                held = {p.symbol: p.quantity for p in app.snapshot(profile, account).positions}
                self.assertGreaterEqual(held.get('ACME', 0), 2)


if __name__ == '__main__':
    unittest.main()
