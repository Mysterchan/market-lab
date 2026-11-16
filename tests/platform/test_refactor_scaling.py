"""Behavioral regressions for separated state, bounded hot paths and background jobs."""

from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory
from threading import Event
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from sim_platform import Platform, Intent
from sim_platform.domain.models import LedgerEvent
from sim_platform.research import ResearchSession
from sim_platform.examples.market_demo import write_fixture
from sim_platform.persistence.sql.repositories import EventRepository
from sim_platform.persistence.sql.migrations import v0005_projections
from sim_platform.domain.ledger import replay
from sim_platform.domain.ledger.checkpoint import encode_state
from sim_platform.market.history import MarketHistory
from sim_platform.market.models import MarketTrade
from sim_platform.research.jobs import BackgroundJob, Cancellation
from sim_platform.research.batch import compare


class RefactorTests(unittest.TestCase):
    def research_session(self, directory, library=None):
        platform = Platform(directory)
        self.addCleanup(platform.close)
        return ResearchSession(platform, library)

    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source, self.news = write_fixture(self.root / 'fixture')
        self.session = self.research_session(self.root / 'accounts')
        self.addCleanup(self.session.close)
        self.app = self.session.app
        self.profile = self.app.create_profile('Research')
        self.account = self.app.create_account(self.profile, 'Account', 100_000_000)
        self.session.load(self.source, news=self.news)

    def start(self):
        self.session.start(self.profile, self.account, policy='human', execution_mode='controlled')
        for _ in range(3):
            self.session.step()
        return self.session.runner

    def test_export_does_not_invalidate_prepared_decision_or_change_market(self):
        runner = self.start()
        handle = runner.controller()
        request = handle.prepare()
        before = self.session.market.snapshot
        self.session.export(self.root / 'report.json')
        record = handle.respond(request, {'actions': [], 'state': {'valid': True}})
        self.assertEqual(record['status'], 'ok')
        self.assertEqual(self.session.market.snapshot, before)
        with closing(sqlite3.connect(self.root / 'accounts/platform.sqlite')) as db, db:
            journal = json.loads(db.execute('SELECT policy_state FROM runs').fetchone()[0])
            self.assertNotIn('research_archive', journal)

    def test_execution_hot_path_does_not_scan_order_event_history(self):
        runner = self.start()
        original = EventRepository.for_run

        def bounded(repo, run_id, *, kinds=None, limit=None):
            if limit is None:
                raise AssertionError('unbounded event read in execution')
            return original(repo, run_id, kinds=kinds, limit=limit)

        with patch.object(EventRepository, 'for_run', bounded):
            self.session.order('test', 'buy', 1, 1_901_000)
            runner.step()
            runner.snapshot

    def test_checkpoint_size_does_not_grow_with_terminal_orders_and_history_survives(self):
        maker = self.app.create_maker(self.profile, 'Manual', 'human', '1')
        run = self.app.create_run(self.profile, self.account, maker, ['AAPL'])
        handle = self.app.decision_session(self.profile, run, maker)
        for index in range(40):
            intent_id = handle.submit(Intent(str(index), 'AAPL', 'buy', 1, 100), index)
            self.app._fixture_event(
                self.profile,
                run,
                'accept-' + str(index),
                index,
                'accepted',
                dict(intent_id=intent_id, order_id='order-' + str(index)),
            )
            self.app._fixture_event(
                self.profile,
                run,
                'cancel-' + str(index),
                index,
                'cancelled',
                dict(order_id='order-' + str(index), reason='test'),
            )
        self.app.finish_run(self.profile, run)
        expected = self.app.snapshot(self.profile, self.account)
        self.assertEqual(len(expected.orders), 40)
        self.assertEqual(
            expected,
            self.app.replay_snapshot(
                self.profile, self.account, self.app.events(self.profile, self.account)
            ),
        )
        with closing(sqlite3.connect(self.root / 'accounts/platform.sqlite')) as db, db:
            raw = db.execute('SELECT state FROM account_checkpoints').fetchone()[0]
            self.assertLess(len(raw), 2000)
            self.assertEqual(json.loads(raw)['orders'], {})
        with Platform(self.root / 'accounts') as reopened:
            self.assertEqual(reopened.snapshot(self.profile, self.account), expected)
            self.assertEqual(reopened.rebuild_account(self.profile, self.account), expected)

    def test_stale_compact_checkpoint_rebuilds_against_current_projection(self):
        runner = self.start()
        with closing(sqlite3.connect(self.root / 'accounts/platform.sqlite')) as db, db:
            old = db.execute('SELECT state FROM account_checkpoints').fetchone()[0]
        self.session.order('late', 'buy', 1, 1_901_000)
        runner.step()
        expected = self.app.snapshot(self.profile, self.account)
        with closing(sqlite3.connect(self.root / 'accounts/platform.sqlite')) as db, db:
            db.execute('UPDATE account_checkpoints SET state=?', (old,))
        self.assertEqual(self.app.snapshot(self.profile, self.account), expected)

    def test_saved_view_avoids_full_report_and_stream_export_matches_full_report(self):
        runner = self.start()
        for i in range(110):
            runner.act([], {'i': i})
        self.session.stop()
        expected = self.session.report()
        with patch.object(
            EventRepository, 'for_account', side_effect=AssertionError('full ledger')
        ):
            self.session.view_run(self.profile, self.session.run_id)
            view = self.session.view()
            self.assertEqual(len(view['decisions']), 100)
            self.assertEqual(view['summary'], expected['summary'])
            self.session.export(self.root / 'stream.json')
        self.assertEqual(json.loads((self.root / 'stream.json').read_text()), expected)

    def test_v4_upgrade_preserves_history_archive_and_failure_rolls_back(self):
        self.start()
        self.session.export(self.root / 'before.json')
        self.session.stop()
        expected = self.session.report()
        state = encode_state(
            replay(LedgerEvent.from_record(e) for e in self.app.events(self.profile, self.account))
        )
        run_id = self.session.run_id
        self.session.close()
        database = self.root / 'accounts/platform.sqlite'
        with closing(sqlite3.connect(database)) as db, db:
            archive = json.loads(
                db.execute(
                    'SELECT data FROM research_archives WHERE run_id=?', (run_id,)
                ).fetchone()[0]
            )
            journal = json.loads(
                db.execute('SELECT policy_state FROM runs WHERE id=?', (run_id,)).fetchone()[0]
            )
            journal['research_archive'] = archive['market']
            db.execute('UPDATE runs SET policy_state=? WHERE id=?', (json.dumps(journal), run_id))
            db.execute('UPDATE account_checkpoints SET state=?', (json.dumps(state),))
            db.execute('DROP TABLE agent_experiments')
            db.execute('DROP TABLE agent_revisions')
            db.execute('DROP TABLE run_checkpoints')
            db.execute('DROP TABLE ledger_items')
            db.execute('DROP TABLE research_archives')
            db.execute("UPDATE alembic_version SET version_num='0004_incremental'")
            db.execute('PRAGMA user_version=4')
        before = database.read_bytes()
        upgrade = v0005_projections.upgrade

        def failed(connection):
            upgrade(connection)
            raise OSError('migration failure')

        with patch.object(v0005_projections, 'upgrade', failed), self.assertRaises(OSError):
            Platform(self.root / 'accounts')
        self.assertEqual(database.read_bytes(), before)
        with Platform(self.root / 'accounts') as reopened:
            from sim_platform.research.reports import run_report

            self.assertEqual(run_report(reopened, self.profile, run_id), expected)

    def test_cancelled_comparison_does_not_start_pending_runs(self):
        token = Cancellation()
        token.cancel()
        with patch(
            'sim_platform.research.batch.ResearchBatch.evaluate',
            side_effect=AssertionError('started'),
        ):
            result = compare(self.session.app, self.source, cancellation=token)
        self.assertEqual(result['status'], 'cancelled')
        self.assertEqual(len(result['pending_configurations']), 2)
        self.assertEqual(result['results'], [])

    def test_compact_comparison_persists_independent_runs(self):
        result = compare(self.session.app, self.source, include_details=False)
        with Platform(self.root / 'accounts') as app:
            for entry in result['results']:
                report = entry['report']
                self.assertEqual(
                    app.run(report['run']['profile_id'], report['run']['id'])['status'], 'completed'
                )
                self.assertNotIn('run_events', report)
        self.assertNotEqual(
            result['results'][0]['report']['run']['id'], result['results'][1]['report']['run']['id']
        )


class HistoryAndJobTests(unittest.TestCase):
    def test_incremental_bars_match_independent_history_after_break_and_historical_queries(self):
        from sim_platform.persistence.workspace.temporary import AppWorkspace

        workspace = AppWorkspace()
        self.addCleanup(workspace.close)
        history = MarketHistory(workspace.market_cache())
        self.addCleanup(history.close)
        trades = []
        for seq in range(1, 81):
            trade = MarketTrade(
                seq, seq * 10, 2**63 + seq, 100 + seq % 7, seq % 4, seq % 3 != 0, 'P'
            )
            trades.append(trade)
            history.add_trade(trade)
            if seq % 9 == 0:
                history.bars(100, seq * 10, seq * 10, limit=3)
        history.break_trade(trades[10].match_id, 810)
        history.add_trade(MarketTrade(81, 820, 999, 120, 3, True, 'P'))
        trades.append(MarketTrade(81, 820, 999, 120, 3, True, 'P'))
        for cutoff, start in [(800, 0), (820, 0), (820, 117), (820, 0)]:
            expected = {}
            for trade in trades:
                if (
                    not start <= trade.timestamp_ns <= cutoff
                    or not trade.printable
                    or not trade.quantity
                ):
                    continue
                if trade.match_id == trades[10].match_id and cutoff >= 810:
                    continue
                expected.setdefault(trade.timestamp_ns // 100 * 100, []).append(trade)
            actual = history.bars(100, cutoff, 820, start)
            self.assertEqual(len(actual), len(expected))
            for bar, (stamp, values) in zip(actual, sorted(expected.items())):
                self.assertEqual(
                    (bar.start_ns, bar.open, bar.high, bar.low, bar.close, bar.volume),
                    (
                        stamp,
                        values[0].price,
                        max(t.price for t in values),
                        min(t.price for t in values),
                        values[-1].price,
                        sum(t.quantity for t in values),
                    ),
                )
        self.assertEqual(history.trades(820, limit=3), history.trades(820)[-3:])

    def test_background_job_cancels_and_joins_without_sharing_owner_thread(self):
        job = BackgroundJob()
        self.addCleanup(job.close)
        started = Event()

        def operation(*, cancellation):
            started.set()
            cancellation._event.wait(2)
            return {'cancelled': cancellation.cancelled}

        job.start(operation)
        self.assertTrue(started.wait(2))
        job.cancel()
        job.future.result(timeout=2)
        self.assertEqual(job.take_result(), {'cancelled': True})
        self.assertFalse(job.active)


if __name__ == '__main__':
    unittest.main()
