"""Shared controller, historical reports, independent comparison and exports."""

from pathlib import Path
import json
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from sim_platform import Platform, ContractError
from sim_platform.research import ResearchSession, run_report, export_report
from sim_platform.research.batch import compare
from sim_platform.examples.market_demo import write_fixture

LIBRARY = None
if __name__ == '__main__' and len(sys.argv) > 1 and not sys.argv[1].startswith('-'):
    LIBRARY = sys.argv.pop(1)


class ResearchTests(unittest.TestCase):
    def research_session(self, directory, library=None):
        platform = Platform(directory)
        self.addCleanup(platform.close)
        return ResearchSession(platform, library)

    def setUp(self):
        self.temp = TemporaryDirectory(prefix='research-core-')
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.source, self.news = write_fixture(self.path / 'fixture')
        self.c = self.research_session(self.path / 'accounts', LIBRARY)
        self.addCleanup(self.c.close)
        self.profile = self.c.create_profile('Researcher')
        self.account = self.c.create_account(self.profile, 'Account', 100_000_000)
        self.c.load(self.source, news=self.news)

    def drain(self):
        self.c.play()
        while self.c.runner.snapshot.status == 'running':
            self.c.tick()

    def test_research_playback_does_not_trade(self):
        self.c.step()
        self.c.play()
        self.c.tick()
        self.c.pause()
        before = self.c.view()
        self.c.tick()
        self.assertEqual(self.c.view(), before)
        self.assertEqual(self.c.app.snapshot(self.profile, self.account).cash, 100_000_000)
        self.assertEqual(self.c.catalog(self.profile)['runs'], [])

    def prepare(self, **arguments):
        self.c.begin_prepare(self.source, news=self.news, **arguments)
        while self.c.preparation['status'] == 'preparing':
            self.c.prepare_tick()

    def test_first_book_preparation_skips_account_writes_and_binds_observed_book(self):
        import struct

        clock = b'S' + struct.pack('>HH', 0, 0) + (1).to_bytes(6, 'big') + b'O'
        self.source.write_bytes(
            (struct.pack('>H', len(clock)) + clock) * 20_000 + self.source.read_bytes()
        )
        before = self.c.app.snapshot(self.profile, self.account)
        self.prepare(start_mode='first_book')
        self.assertEqual(self.c.market.snapshot.sequence, 20_003)
        self.assertEqual(self.c.market.status, 'paused')
        self.assertEqual(self.c.app.snapshot(self.profile, self.account), before)
        self.assertEqual(self.c.catalog(self.profile)['runs'], [])
        origin = self.c.market.snapshot
        view = self.c.start(self.profile, self.account, execution_mode='controlled')
        self.assertEqual(view['market']['sequence'], origin.sequence)
        self.assertEqual(view['execution']['market_sequence'], origin.sequence)
        events = self.c.app.run_events(self.profile, self.c.run_id)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['data']['market_sequence'], origin.sequence)
        self.assertEqual(events[0]['time_ns'], origin.time_ns)
        self.assertEqual(view['decisions'], [])
        self.c.order('prepared-buy', 'buy', 10, 1_901_000)
        self.c.step()  # publication precedes the next ITCH message
        self.c.step()  # execution updates the historical book and fills the order
        self.assertEqual(self.c.view()['account']['positions'][0]['quantity'], 10)
        self.c.stop()
        report = self.c.report()
        self.assertEqual(report['source']['start_origin']['sequence'], origin.sequence)
        self.assertEqual(
            report['final']['cash'], self.c.app.snapshot(self.profile, self.account).cash
        )

    def test_custom_start_preserves_prior_history_without_looking_past_start(self):
        start = (9 * 3600 + 30 * 60 + 4) * 1_000_000_000
        self.prepare(start_mode='custom', start_time_ns=start)
        view = self.c.view()
        self.assertEqual(view['market']['time_ns'], start)
        self.assertEqual(view['market']['sequence'], 4)
        self.assertEqual(len(view['trades']), 1)
        self.assertEqual(len(view['news']), 1)
        self.assertTrue(all(t['timestamp_ns'] <= start for t in view['trades']))
        self.c.start(self.profile, self.account, policy='rule', execution_mode='controlled')
        self.assertEqual(self.c.view()['decisions'], [])
        self.c.step()
        self.assertEqual(self.c.view()['market']['sequence'], 5)

    def test_regular_start_and_cancel_or_unreachable_start_create_no_runs(self):
        self.prepare(start_mode='regular')
        self.assertEqual(self.c.market.time_ns, 34_200_000_000_000)
        self.assertEqual(self.c.market.snapshot.sequence, 1)
        self.c.begin_prepare(self.source, start_mode='first_book')
        if self.c.preparation['status'] == 'preparing':
            self.c.prepare_tick(max_events=1)
        self.c.cancel_preparation()
        with self.assertRaises(ContractError):
            self.c.start(self.profile, self.account, execution_mode='controlled')
        self.assertEqual(self.c.catalog(self.profile)['runs'], [])
        with self.assertRaisesRegex(ContractError, 'ends before'):
            self.prepare(start_mode='custom', start_time_ns=40_000_000_000_000)
        self.assertEqual(self.c.catalog(self.profile)['runs'], [])

    def test_start_from_preview_reprepares_the_selected_origin(self):
        self.prepare(start_mode='first_book')
        origin = self.c.market.snapshot
        self.c.step()
        view = self.c.start_from_beginning(
            profile=self.profile, account=self.account, execution_mode="controlled"
        )
        self.assertEqual(view['market']['sequence'], origin.sequence)
        self.assertEqual(view['market']['time_ns'], origin.time_ns)

    def test_untouched_preview_is_reused_when_starting_an_experiment(self):
        self.prepare(start_mode='first_book')
        market = self.c.market
        origin = market.snapshot
        with patch.object(
            self.c, '_market_factory', side_effect=AssertionError('preview loaded twice')
        ):
            view = self.c.start_from_beginning(
                profile=self.profile, account=self.account, execution_mode="controlled"
            )
        self.assertIs(self.c.market, market)
        self.assertEqual(view['market']['sequence'], origin.sequence)
        self.assertEqual(view['market']['time_ns'], origin.time_ns)
        self.assertEqual(len(view['events']), 1)

    def test_preview_reuse_requires_unchanged_files_and_start_settings(self):
        import os

        self.prepare(start_mode='first_book')
        arguments = dict(path=self.source, news=self.news, start_mode='first_book')
        self.assertTrue(self.c.can_reuse_preparation(**arguments))
        for changes in (
            dict(symbol='MSFT'),
            dict(news=None),
            dict(allow_implicit_eof=True),
            dict(start_mode='custom', start_time_ns=34_200_000_000_000),
        ):
            self.assertFalse(self.c.can_reuse_preparation(**{**arguments, **changes}))
        self.news.write_text(self.news.read_text() + ' ')
        self.assertFalse(self.c.can_reuse_preparation(**arguments))
        self.prepare(start_mode='first_book')
        stat = self.source.stat()
        os.utime(self.source, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
        self.assertFalse(self.c.can_reuse_preparation(**arguments))

    def test_start_after_preview_explicitly_restarts_data(self):
        self.c.step()
        view = self.c.start_from_beginning(
            profile=self.profile, account=self.account, execution_mode="controlled"
        )
        self.assertEqual(view['market']['time_ns'], 0)
        self.assertEqual(view['execution']['status'], 'running')
        with self.assertRaises(ContractError):
            self.c.start(self.profile, self.account, execution_mode='controlled')

    def test_batched_ticks_preserve_execution_and_decision_boundaries(self):
        other = self.research_session(self.path / 'batched', LIBRARY)
        profile = other.create_profile('Researcher')
        account = other.create_account(profile, 'Account', 100_000_000)
        other.load(self.source, news=self.news)
        self.c.start(self.profile, self.account, policy='rule', execution_mode='controlled')
        other.start(profile, account, policy='rule', execution_mode='controlled')
        self.drain()
        other.play()
        view = other.tick(max_events=1000, budget_seconds=10)
        expected = self.c.view()
        self.assertEqual(view['market'], expected['market'])
        self.assertEqual(view['trades'], expected['trades'])
        for key in ('cash', 'fees', 'realized_pnl', 'positions'):
            self.assertEqual(view['account'][key], expected['account'][key])
        self.assertEqual(
            [(e['kind'], e['time_ns'], e['data'].get('market_sequence')) for e in view['events']],
            [
                (e['kind'], e['time_ns'], e['data'].get('market_sequence'))
                for e in expected['events']
            ],
        )
        self.assertEqual(
            [
                (d['observation']['boundary'], d['observation']['trigger'], d['status'])
                for d in view['decisions']
            ],
            [
                (d['observation']['boundary'], d['observation']['trigger'], d['status'])
                for d in expected['decisions']
            ],
        )

    def test_batched_ticks_yield_at_budget_and_paused_ticks_do_not_advance(self):
        self.c.play()
        with patch('sim_platform.market.pacing.monotonic', side_effect=[0, 0.01, 0.04]):
            view = self.c.tick(max_events=100, budget_seconds=0.035)
        self.assertEqual(view['market']['sequence'], 2)
        self.c.pause()
        before = self.c.view()
        self.assertEqual(self.c.tick(max_events=100, budget_seconds=0.035), before)

    def test_human_handoff_and_atomic_export_full_journal(self):
        self.c.start(self.profile, self.account, fee_per_share=100, execution_mode='controlled')
        for _ in range(3):
            self.c.step()
        self.c.order('buy', 'buy', 10, 1_901_000)
        self.c.step()
        self.c.step()
        before = self.c.runner.snapshot.account
        self.c.handoff('hold', hold_ns=1_000_000_000)
        self.assertEqual(self.c.runner.snapshot.account, before)
        self.drain()
        report = self.c.report()
        self.assertEqual(report['final']['cash'], 99_988_000)
        self.assertEqual(report['summary']['handoff_count'], 1)
        self.assertEqual(report['source']['path'], str(self.source.resolve()))
        self.assertEqual(len(report['market_data']['trades']), 1)
        destination = self.path / 'report.json'
        self.c.export(destination)
        self.assertEqual(json.loads(destination.read_text()), report)
        self.c.export(self.path / 'events.csv')
        self.assertIn('simulation_time_ns', (self.path / 'events.csv').read_text())

    def test_closed_run_reopens_read_only_with_market_archive(self):
        self.c.start(self.profile, self.account, policy='rule', execution_mode='controlled')
        self.drain()
        run = self.c.run_id
        report = self.c.report()
        with_session = self.research_session(self.path / 'accounts')
        self.addCleanup(with_session.close)
        view = with_session.view_run(self.profile, run)
        self.assertEqual(view['mode'], 'saved')
        self.assertEqual(view['account'], report['final'])
        self.assertTrue(view['bars'])
        for operation in (with_session.play, with_session.step):
            with self.assertRaises(ContractError):
                operation()

    def test_historical_report_does_not_include_later_account_run(self):
        self.c.start(
            self.profile,
            self.account,
            policy='rule',
            fee_per_share=100,
            execution_mode='controlled',
        )
        self.drain()
        first = self.c.run_id
        expected = self.c.report()
        self.c.load(self.source)
        self.c.start(
            self.profile,
            self.account,
            policy='rule',
            fee_per_share=100,
            execution_mode='controlled',
        )
        self.drain()
        self.assertNotEqual(self.c.report()['final']['cash'], expected['final']['cash'])
        self.assertEqual(run_report(self.c.app, self.profile, first)['final'], expected['final'])

    def test_controller_and_cli_evaluation_match_observations_and_account(self):
        self.c.start(
            self.profile,
            self.account,
            policy='rule',
            fee_per_share=100,
            execution_mode='controlled',
        )
        self.drain()
        expected = self.c.report()
        process = subprocess.run(
            [
                sys.executable,
                '-m',
                'sim_platform',
                '--data-dir',
                str(self.path / 'cli'),
                'evaluate',
                '--input',
                str(self.source),
                '--news',
                str(self.news),
            ]
            + (['--library', LIBRARY] if LIBRARY else []),
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        actual = json.loads(process.stdout)
        self.assertEqual(actual['summary'], expected['summary'])
        self.assertEqual(
            [d['fingerprint'] for d in actual['decisions']],
            [d['fingerprint'] for d in expected['decisions']],
        )

    def test_independent_equal_start_comparison_and_cli(self):
        result = compare(self.c.app, str(self.source), news=str(self.news), library=LIBRARY)
        self.assertEqual(len(result['results']), 2)
        reports = [r['report'] for r in result['results']]
        self.assertEqual([r['initial']['cash'] for r in reports], [100_000_000] * 2)
        self.assertNotEqual(reports[0]['run']['account_id'], reports[1]['run']['account_id'])
        self.assertTrue(all(not r['handoffs'] for r in reports))
        destination = self.path / 'comparison.json'
        export_report(result, destination)
        self.assertEqual(json.loads(destination.read_text())['experiment'], result['experiment'])
        with self.assertRaisesRegex(ContractError, 'input'):
            export_report(result, self.news)
        process = subprocess.run(
            [sys.executable, '-m', 'sim_platform', 'compare', '--input', str(self.source)]
            + (['--library', LIBRARY] if LIBRARY else []),
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(len(json.loads(process.stdout)['results']), 2)

    def test_export_failure_preserves_old_file_and_removes_temp(self):
        self.c.start(self.profile, self.account, execution_mode='controlled')
        self.c.stop()
        destination = self.path / 'report.json'
        destination.write_text('old')
        with patch('sim_platform.persistence.export_files.os.replace', side_effect=OSError('disk')):
            with self.assertRaises(OSError):
                self.c.export(destination)
        self.assertEqual(destination.read_text(), 'old')
        self.assertEqual(list(self.path.glob('*.tmp')), [])

    def test_input_and_news_cannot_be_overwritten_even_from_saved_view(self):
        self.c.start(self.profile, self.account, execution_mode='controlled')
        self.c.stop()
        self.c.view_run(self.profile, self.c.run_id)
        original = self.news.read_bytes()
        with self.assertRaisesRegex(ContractError, 'input'):
            self.c.export(self.news)
        self.assertEqual(self.news.read_bytes(), original)

    def test_stop_cancels_open_orders_and_keeps_reopenable_run(self):
        self.c.start(self.profile, self.account, execution_mode='controlled')
        for _ in range(3):
            self.c.step()
        self.c.order('passive', 'buy', 1, 1)
        self.c.stop()
        report = self.c.report()
        self.assertEqual(report['run']['status'], 'stopped')
        self.assertEqual(report['final']['orders'][0]['status'], 'cancelled')

    def test_archive_failure_still_stops_and_cancels_orders(self):
        self.c.start(self.profile, self.account, execution_mode='controlled')
        for _ in range(3):
            self.c.step()
        self.c.order('passive', 'buy', 1, 1)
        with patch.object(
            self.c._controller, '_archive', side_effect=OSError('archive storage failed')
        ):
            with self.assertRaises(OSError):
                self.c.stop()
        self.assertEqual(self.c.runner.snapshot.status, 'stopped')
        self.assertEqual(self.c.runner.snapshot.account.orders[0].status, 'cancelled')

    def test_active_run_cannot_be_displaced_by_load_or_saved_view(self):
        self.c.start(self.profile, self.account, execution_mode='controlled')
        for operation in (
            lambda: self.c.load(self.source),
            lambda: self.c.view_run(self.profile, self.c.run_id),
        ):
            with self.assertRaises(ContractError):
                operation()
        self.assertEqual(self.c.runner.snapshot.status, 'running')


if __name__ == '__main__':
    unittest.main()
