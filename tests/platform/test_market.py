"""Market verification: native book parity, clock, point-in-time queries and integrity."""

from dataclasses import asdict, FrozenInstanceError
import csv
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
from itch_fixtures import directory, add, reduce, delete, replace, frame, common, stock
from sim_platform import Platform
from sim_platform.market import (
    MarketError,
    FutureDataError,
    DataUnavailable,
    FeedRecord,
    SequencedFeed,
    NewsRecord,
)
from sim_platform.examples.market_demo import write_fixture

LIBRARY = None
REPLAY = ROOT / 'build/itch_replay'
if __name__ == '__main__' and len(sys.argv) > 1 and not sys.argv[1].startswith('-'):
    LIBRARY = sys.argv.pop(1)
    if len(sys.argv) > 1 and not sys.argv[1].startswith('-'):
        REPLAY = Path(sys.argv.pop(1))


class MarketTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'aapl.bin'
        self.app = Platform(Path(self.temp.name) / 'platform')
        self.addCleanup(self.app.close)

    def session(self, data, *, news=(), complete=True, **kwargs):
        self.path.write_bytes(data + (b'\0\0' if complete else b''))
        session = self.app.open_market('AAPL', library=LIBRARY)
        self.addCleanup(session.close)
        session.load(self.path, news=news, **kwargs)
        return session

    def feed(self):
        return (
            directory('AAPL', timestamp=1)
            + add(1, 'B', 100, 1000000, 'AAPL', timestamp=2)
            + add(2, 'S', 80, 1001000, 'AAPL', timestamp=3)
            + reduce('E', 1, 20, timestamp=4)
        )

    def drain(self, session):
        session.play()
        events = []
        session.subscribe(events.append)
        while session.status == 'playing':
            session.pump(10)
        return events

    def test_step_pause_resume_and_immutable_snapshot(self):
        s = self.session(self.feed())
        self.assertEqual((s.status, s.time_ns), ('ready', 0))
        s.step()
        s.step()
        self.assertEqual(s.book().bids, ((1000000, 100),))
        with self.assertRaises(FrozenInstanceError):
            s.snapshot.time_ns = 999
        s.play()
        s.pause()
        before = s.snapshot
        self.assertEqual(s.pump(10), ())
        self.assertEqual(s.snapshot, before)
        s.play()
        s.pump(10)
        self.assertEqual(s.status, 'completed')
        self.assertEqual(s.book().bids, ((1000000, 80),))
        with self.assertRaises(MarketError):
            s.play()

    def test_native_book_matches_v1_every_event(self):
        priced = reduce('C', 2, 10, timestamp=5)
        priced = priced[:25] + (124).to_bytes(8, 'big') + priced[33:]
        data = (
            self.feed()
            + priced
            + reduce('X', 1, 10, timestamp=6)
            + replace(1, 3, 25, 999000, timestamp=7)
            + delete(2, timestamp=8)
        )
        s = self.session(data)
        snapshots = []
        s.subscribe(
            lambda e: (
                snapshots.append(e.snapshot)
                if e.source_type in 'AECXDUF' and e.kind == 'market'
                else None
            )
        )
        self.drain(s)
        if not REPLAY.exists():
            self.skipTest('the native replay executable is not built (Windows builds the library)')
        result = subprocess.run(
            [str(REPLAY), '--input', str(self.path), '--symbol', 'AAPL', '--depth', '5'],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = list(csv.DictReader(io.StringIO(result.stdout)))
        self.assertEqual(len(rows), len(snapshots))
        for row, snapshot in zip(rows, snapshots):

            def depth(values):
                return '|'.join(f'{p // 10000}.{p % 10000:04d}:{q}' for p, q in values)

            self.assertEqual(row['bid_depth'], depth(snapshot.bids))
            self.assertEqual(row['ask_depth'], depth(snapshot.asks))
            self.assertEqual(int(row['frame']), snapshot.sequence)
            self.assertEqual(int(row['timestamp_ns']), snapshot.time_ns)

    def test_future_queries_rejected(self):
        s = self.session(self.feed())
        s.step()
        for query in (s.book, s.trades, s.bars, s.news):
            with self.subTest(query=query.__name__), self.assertRaises(FutureDataError):
                query(as_of_ns=2)
        with self.assertRaises(FutureDataError):
            s.trades(start_ns=2)
        with self.assertRaises(DataUnavailable):
            s.book()

    def test_equal_time_publication_and_other_symbol_clock(self):
        s = self.session(
            directory('AAPL', timestamp=1)
            + directory('MSFT', locate=8, timestamp=2)
            + add(1, 'B', 100, 1000000, 'AAPL', timestamp=3),
            news=[NewsRecord('report', 3, '2026-Q1', 'published')],
        )
        s.step()
        self.assertEqual(s.step().kind, 'clock')
        self.assertEqual(s.time_ns, 2)
        self.assertEqual(s.step().kind, 'news')
        self.assertEqual(len(s.news()), 1)
        self.assertEqual(s.step().source_type, 'A')

    def test_duplicate_match_and_unknown_break_fail_closed(self):
        tails = [
            reduce('E', 1, 10, timestamp=5),
            frame(common('B', timestamp=5) + (999).to_bytes(8, 'big')),
        ]
        for tail in tails:
            with self.subTest(tail=tail):
                s = self.session(self.feed() + tail)
                self.drain(s)
                self.assertEqual(s.status, 'failed')
                with self.assertRaises(DataUnavailable):
                    s.trades()

    def test_complete_bars_do_not_invent_empty_intervals(self):
        data = self.feed() + frame(common('S', locate=0, timestamp=30) + b'M')
        s = self.session(data)
        self.drain(s)
        self.assertEqual(len(s.bars(10)), 1)
        self.assertTrue(s.bars(10)[0].complete)
        self.assertEqual(s.snapshot.system_event, 'M')
        self.assertEqual(s.snapshot.time_of_day, '00:00:00.000000030')

    def test_trading_halt_and_resume_state(self):
        data = self.feed() + frame(common('H', timestamp=5) + stock('AAPL') + b'H' + b' ' + b'T1  ')
        data += frame(common('H', timestamp=6) + stock('AAPL') + b'T' + b' ' + b'    ')
        s = self.session(data)
        for _ in range(5):
            s.step()
        self.assertEqual(s.snapshot.trading_state, 'H')
        s.step()
        self.assertEqual(s.snapshot.trading_state, 'T')

    def test_bad_news_fixture_fails_on_load(self):
        self.path.write_bytes(self.feed() + b'\0\0')
        for news in (
            [NewsRecord('x', -1, '2026', 'headline')],
            [NewsRecord('x', 2, '2026', 'a'), NewsRecord('x', 3, '2026', 'b')],
        ):
            s = self.app.open_market(library=LIBRARY)
            self.addCleanup(s.close)
            with self.assertRaises(MarketError):
                s.load(self.path, news=news)
            self.assertEqual(s.status, 'failed')

    def test_publication_and_revision_visible_only_when_available(self):
        news = (
            NewsRecord('report', 3, '2025-Q4', 'first'),
            NewsRecord('report', 5, '2025-Q4', 'revision', 2),
        )
        s = self.session(self.feed(), news=news)
        s.step()
        s.step()
        self.assertEqual(s.news(), ())
        e = s.step()
        self.assertEqual((e.kind, s.time_ns), ('news', 3))
        self.assertEqual(len(s.news()), 1)
        s.step()
        s.step()
        self.assertEqual(len(s.news()), 1)
        s.step()
        self.assertEqual(len(s.news()), 2)
        self.assertEqual(len(s.news(as_of_ns=3)), 1)

    def test_trades_nonprintable_cross_and_broken_as_of(self):
        data = self.feed()
        data += frame(
            common('C', timestamp=5)
            + (2).to_bytes(8, 'big')
            + (10).to_bytes(4, 'big')
            + (124).to_bytes(8, 'big')
            + b'N'
            + (1002000).to_bytes(4, 'big')
        )
        data += frame(
            common('P', timestamp=6)
            + (0).to_bytes(8, 'big')
            + b'B'
            + (5).to_bytes(4, 'big')
            + stock('AAPL')
            + (1003000).to_bytes(4, 'big')
            + (125).to_bytes(8, 'big')
        )
        data += frame(
            common('Q', timestamp=7)
            + (30).to_bytes(8, 'big')
            + stock('AAPL')
            + (1004000).to_bytes(4, 'big')
            + (126).to_bytes(8, 'big')
            + b'O'
        )
        data += frame(common('B', timestamp=8) + (125).to_bytes(8, 'big'))
        s = self.session(data)
        self.drain(s)
        self.assertEqual(s.status, 'completed')
        self.assertEqual([t.match_id for t in s.trades()], [123, 126])
        self.assertEqual([t.match_id for t in s.trades(as_of_ns=7)], [123, 125, 126])
        self.assertEqual(len(s.trades(include_nonprintable=True)), 3)
        bar = s.bars(10)[0]
        self.assertEqual(
            (bar.open, bar.high, bar.low, bar.close, bar.volume, bar.count, bar.complete),
            (1000000, 1004000, 1000000, 1004000, 50, 2, False),
        )
        self.assertEqual(s.book().asks, ((1001000, 70),))

    def test_gap_disables_queries_and_notifies(self):
        records = [
            FeedRecord(1, directory('AAPL')[2:]),
            FeedRecord(3, add(1, 'B', 10, 1000000, 'AAPL')[2:]),
        ]
        s = self.app.open_market(library=LIBRARY)
        self.addCleanup(s.close)
        s.load_stream(SequencedFeed(records))
        events = self.drain(s)
        self.assertEqual(s.status, 'failed')
        self.assertEqual(events[-1].kind, 'integrity')
        self.assertIn('gap', s.snapshot.error)
        for query in (s.book, s.trades, s.news, s.bars):
            with self.assertRaises(DataUnavailable):
                query()
        self.assertEqual(s.time_ns, 1)

    def test_unknown_order_desynchronizes(self):
        s = self.session(directory('AAPL') + delete(999))
        self.drain(s)
        self.assertEqual(s.status, 'failed')
        self.assertIn('unknown order', s.snapshot.error)

    def test_backwards_time_fails_without_advancing(self):
        s = self.session(
            directory('AAPL', timestamp=10) + add(1, 'B', 10, 1000000, 'AAPL', timestamp=2)
        )
        self.drain(s)
        self.assertEqual(s.status, 'failed')
        self.assertEqual(s.time_ns, 10)

    def test_truncation_unknown_length_and_trailing_bytes(self):
        for tail in (b'\x00', frame(b'Z' + b'\0' * 10), frame(common('A')), b'\0\0x'):
            with self.subTest(tail=tail):
                s = self.session(directory('AAPL') + tail, complete=False)
                self.drain(s)
                self.assertEqual(s.status, 'failed')

    def test_eof_policy(self):
        s = self.session(self.feed(), complete=False)
        self.drain(s)
        self.assertEqual(s.status, 'failed')
        s = self.session(self.feed(), complete=False, allow_implicit_eof=True)
        self.drain(s)
        self.assertEqual(s.status, 'failed')
        s = self.session(
            self.feed() + frame(common('S', locate=0, timestamp=5) + b'C'),
            complete=False,
            allow_implicit_eof=True,
        )
        self.drain(s)
        self.assertEqual(s.status, 'completed')
        self.assertEqual(s.snapshot.integrity, 'ordered_unverified')

    def test_native_file_peek_keeps_future_book_private(self):
        from sim_platform.market.native import NativeEngine

        self.path.write_bytes(self.feed() + b'\0\0')
        engine = NativeEngine('AAPL', LIBRARY)
        self.addCleanup(engine.close)
        engine.open_file(self.path)
        first = engine.peek_file()
        self.assertEqual(first, {'sequence': 1, 'timestamp_ns': 1})
        self.assertEqual(engine.peek_file(), first)
        self.assertEqual(engine.next_file()['sequence'], 1)
        engine.next_file()
        self.assertEqual(engine.peek_file()['sequence'], 3)
        self.assertEqual(engine.peek_file()['sequence'], 3)
        result = engine.next_file()
        self.assertEqual(result['book']['bids'], [[1000000, 100]])
        self.assertEqual(result['book']['asks'], [[1001000, 80]])
        engine.next_file()
        self.assertIsNone(engine.peek_file())
        self.assertIsNone(engine.peek_file())
        self.assertIsNone(engine.next_file())

    def test_news_prefetch_does_not_apply_execution(self):
        news = [NewsRecord('x', 3, '2026-Q1', 'published')]
        s = self.session(
            directory('AAPL', timestamp=1)
            + add(1, 'B', 100, 1000000, 'AAPL', timestamp=2)
            + reduce('E', 1, 20, timestamp=4),
            news=news,
        )
        s.step()
        s.step()
        observed = []
        s.subscribe(lambda e: observed.append(s.book().bids) if e.kind == 'news' else None)
        self.assertEqual(s.step().kind, 'news')
        self.assertEqual(observed, [((1000000, 100),)])
        self.assertEqual(s.snapshot.sequence, 2)
        self.assertEqual(s.trades(), ())
        s.play()
        s.pause()
        s.pump(2)
        self.assertEqual(s.book().bids, ((1000000, 100),))
        s.step()
        self.assertEqual(s.book().bids, ((1000000, 80),))
        self.assertEqual(len(s.trades()), 1)

    def test_binaryfile_does_not_use_python_file_reads(self):
        self.path.write_bytes(self.feed() + b'\0\0')
        s = self.app.open_market(library=LIBRARY)
        self.addCleanup(s.close)
        with patch('builtins.open', side_effect=AssertionError('Python must not read BinaryFILE')):
            s.load(self.path)
            self.drain(s)
        self.assertEqual(s.status, 'completed')

    def test_file_and_sequenced_stream_produce_same_committed_events(self):
        source = self.feed()
        records = []
        offset = 0
        while offset < len(source):
            size = int.from_bytes(source[offset : offset + 2], 'big')
            records.append(FeedRecord(len(records) + 1, source[offset + 2 : offset + 2 + size]))
            offset += 2 + size
        news = [NewsRecord('x', 3, '2026', 'published')]
        local = self.session(source, news=news)
        streamed = self.app.open_market(library=LIBRARY)
        self.addCleanup(streamed.close)
        streamed.load_stream(SequencedFeed(records), news=news)

        def events(session):
            values = [asdict(e) for e in self.drain(session)]
            for value in values:
                value['snapshot'].pop('integrity')
            return values

        self.assertEqual(events(local), events(streamed))

    def test_native_and_v1_eof_rules_agree(self):
        end = frame(common('S', locate=0, timestamp=5) + b'C')
        cases = [
            (self.feed(), False),
            (self.feed(), True),
            (self.feed() + end, True),
            (self.feed() + end + b'\0', True),
            (self.feed() + end + b'\0\0x', True),
        ]
        if not REPLAY.exists():
            self.skipTest('the native replay executable is not built (Windows builds the library)')
        for source, allow in cases:
            with self.subTest(source=source, allow=allow):
                s = self.session(source, complete=False, allow_implicit_eof=allow)
                self.drain(s)
                args = [str(REPLAY), '--input', str(self.path), '--symbol', 'AAPL']
                if allow:
                    args.append('--allow-implicit-eof')
                result = subprocess.run(args, capture_output=True)
                self.assertEqual(s.status == 'completed', result.returncode == 0)

    def test_unseen_symbol_fails(self):
        s = self.session(directory('MSFT'))
        self.drain(s)
        self.assertEqual(s.status, 'failed')

    def test_replay_identical(self):
        s1 = self.session(self.feed(), news=[NewsRecord('x', 3, '2026', 'test')])
        first = [asdict(e) for e in self.drain(s1)]
        s2 = self.session(self.feed(), news=[NewsRecord('x', 3, '2026', 'test')])
        second = [asdict(e) for e in self.drain(s2)]
        self.assertEqual(first, second)
        self.assertEqual(s1.bars(), s2.bars())

    def test_subscriber_errors_and_reentrancy_do_not_corrupt_market(self):
        s = self.session(self.feed())
        s.subscribe(lambda e: s.step())
        events = []
        unsub = s.subscribe(events.append)
        s.step()
        unsub()
        size = len(events)
        self.drain(s)
        self.assertEqual(len(events), size)
        self.assertEqual(s.status, 'completed')
        self.assertTrue(s.subscriber_errors)

    def test_close_and_load_failure(self):
        s = self.app.open_market(library=LIBRARY)
        with self.assertRaises(MarketError):
            s.load(Path(self.temp.name) / 'missing')
        self.assertEqual(s.status, 'failed')
        s.close()
        s = self.session(self.feed())
        s.close()
        self.assertEqual(s.status, 'closed')
        with self.assertRaises(DataUnavailable):
            s.trades()

    def test_cli_complete_prototype_without_account_database(self):
        source, news = write_fixture(self.temp.name)
        cmd = [
            sys.executable,
            '-m',
            'sim_platform',
            '--data-dir',
            str(Path(self.temp.name) / 'unused-account-dir'),
            'market',
            '--input',
            str(source),
            '--news',
            str(news),
            '--commands',
            'step 3;book;play;tick 1;pause;snapshot;tick 2;play;run;trades;bars',
        ]
        if LIBRARY:
            cmd += ['--library', LIBRARY]
        result = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        output = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertTrue(
            any(value.get('status') == 'paused' for value in output if isinstance(value, dict))
        )
        self.assertTrue(
            any(value.get('kind') == 'news' for value in output if isinstance(value, dict))
        )
        self.assertEqual(output[-1][0]['volume'], 25)
        self.assertFalse((Path(self.temp.name) / 'unused-account-dir').exists())


if __name__ == '__main__':
    unittest.main()
