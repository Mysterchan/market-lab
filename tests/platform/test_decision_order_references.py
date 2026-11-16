"""Historical audit orders retain exact visibility without repeated stored lists."""

from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from sim_platform import Platform, ContractError, Intent
from sim_platform.decisions import HumanPolicy, BuyHoldSell, submit
from sim_platform.decisions.policy.models import fingerprint
from sim_platform.examples.market_demo import write_fixture
from sim_platform.market import load_news


class DecisionOrderReferenceTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.directory = self.root / 'accounts'
        self.database = self.directory / 'platform.sqlite'
        self.source, self.news = write_fixture(self.root / 'fixture')
        self.app = Platform(self.directory)
        self.addCleanup(self.app.close)
        self.profile = self.app.create_profile('Researcher')
        self.account = self.app.create_account(self.profile, 'Account', 1_000_000_000)

    def start(self, policy=None):
        policy = policy or HumanPolicy()
        maker = self.app.create_maker(self.profile, policy.name, policy.kind, policy.version)
        run = self.app.create_decision_run(self.profile, self.account, maker, 'AAPL', policy)
        market = self.app.open_market('AAPL')
        self.addCleanup(market.close)
        market.load(self.source, news=load_news(self.news))
        runner = self.app.decision_runner(self.profile, run, maker, market, policy)
        self.addCleanup(runner.close)
        return runner, run

    def raw(self, run):
        with closing(sqlite3.connect(self.database)) as db, db:
            return [
                json.loads(row[0])
                for row in db.execute(
                    'SELECT data FROM decision_records WHERE run_id=? ORDER BY decision_index',
                    (run,),
                )
            ]

    def replace_raw(self, run, index, value):
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute(
                'UPDATE decision_records SET data=? WHERE run_id=? AND decision_index=?',
                (json.dumps(value), run, index),
            )

    def drain(self, runner):
        while runner.snapshot.status == 'running':
            runner.step()

    def test_pre_action_accepted_partial_and_cancelled_snapshots_survive_reopen(self):
        runner, run = self.start()
        for _ in range(3):
            runner.step()
        records = [runner.act([submit(Intent('buy', 'AAPL', 'buy', 100, 1_901_000))])]
        records.append(runner.act([]))
        runner.step()  # publication, no fill
        runner.step()  # displayed ask supplies 80 of the requested 100 shares
        records.append(runner.act([]))
        records.append(runner.act([{'kind': 'cancel', 'client_id': 'buy'}]))
        records.append(runner.act([]))
        orders = [r['observation']['execution']['account']['orders'] for r in records]
        self.assertEqual(orders[0], [])  # Newly submitted order is not yet in its input.
        self.assertEqual(
            [(o[0]['status'], o[0]['filled']) for o in orders[1:]],
            [('accepted', 0), ('partial', 80), ('partial', 80), ('cancelled', 80)],
        )
        self.drain(runner)
        for stored, original in zip(self.raw(run), records):
            account = stored['observation']['execution']['account']
            self.assertNotIn('orders', account)
            self.assertEqual(
                account['orders_ref']['sequence'],
                original['observation']['execution']['account']['sequence'],
            )
        self.assertEqual(self.app.decision_records(self.profile, run), records)
        self.app.close()
        with Platform(self.directory) as reopened:
            self.assertEqual(reopened.decision_records(self.profile, run), records)

    def test_filled_orders_from_previous_run_remain_visible(self):
        runner, first = self.start(BuyHoldSell())
        self.drain(runner)
        original = runner.records
        self.assertEqual(
            original[-1]['observation']['execution']['account']['orders'][0]['status'], 'filled'
        )
        next_runner, second = self.start()
        record = next_runner.act([])
        orders = record['observation']['execution']['account']['orders']
        self.assertEqual(len(orders), 2)
        self.assertTrue(all(o['run_id'] == first and o['status'] == 'filled' for o in orders))
        self.assertEqual(self.app.decision_records(self.profile, second), [record])
        self.assertEqual(self.app.decision_records(self.profile, first), original)

    def test_legacy_and_reference_rows_share_tail_report_and_stream_export(self):
        runner, run = self.start(BuyHoldSell())
        self.drain(runner)
        expected = runner.records
        self.replace_raw(run, 1, expected[0])  # Existing full JSON record, no rewrite needed.
        self.assertEqual(runner.records, expected)
        self.assertEqual(self.app.decision_records(self.profile, run, limit=2), expected[-2:])
        self.assertEqual(self.app.run(self.profile, run)['policy_state']['records'], expected)
        self.assertTrue(all(fingerprint(r['observation']) == r['fingerprint'] for r in expected))
        from sim_platform.research.reports import run_report, export_run

        report = run_report(self.app, self.profile, run)
        destination = self.root / 'report.json'
        export_run(self.app, self.profile, run, destination)
        self.assertEqual(json.loads(destination.read_text()), report)
        self.assertNotIn('orders_ref', destination.read_text())

    def test_corrupt_reference_or_missing_events_fail_instead_of_using_latest_orders(self):
        runner, run = self.start()
        record = runner.act([])
        stored = self.raw(run)[0]
        for field, replacement in [
            ('version', 2),
            ('account_id', 'another-account'),
            ('sha256', 'wrong-digest'),
            ('sequence', True),
        ]:
            modified = json.loads(json.dumps(stored))
            modified['observation']['execution']['account']['orders_ref'][field] = replacement
            self.replace_raw(run, 1, modified)
            with self.assertRaises(ContractError):
                runner.records
        self.replace_raw(run, 1, stored)
        self.assertEqual(runner.records, [record])
        self.drain(runner)
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute('DELETE FROM events WHERE account_id=? AND sequence=1', (self.account,))
        with self.assertRaises(ContractError):
            runner.records

    def test_stored_no_action_records_do_not_grow_with_completed_order_count(self):
        runner, run = self.start()
        for _ in range(3):
            runner.step()
        expected = [runner.act([])]
        for index in range(20):
            expected.append(runner.act([submit(Intent(str(index), 'AAPL', 'buy', 1, 1_901_000))]))
            expected.append(runner.act([{'kind': 'cancel', 'client_id': str(index)}]))
            expected.append(runner.act([]))
        raw = self.raw(run)
        sizes = [len(json.dumps(r)) for r in raw if not r['response']['actions']]
        self.assertLess(max(sizes) - min(sizes), 128)
        self.assertEqual(len(expected[-1]['observation']['execution']['account']['orders']), 20)
        self.assertEqual(runner.records, expected)
        self.assertTrue(all('orders' not in r['observation']['execution']['account'] for r in raw))


if __name__ == '__main__':
    unittest.main()
