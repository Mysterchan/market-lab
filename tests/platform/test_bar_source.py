"""CSV price bars: parsing, the synthetic book walk, preparation and multi-day trading."""

from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from sim_platform import ContractError, Platform
from sim_platform.market.models import DAY_NS, REQUOTE, calendar_date
from sim_platform.market.prepared.bars import (
    DAILY_OPEN_NS,
    HALF_SPREAD,
    MAX_STEPS_PER_MOVE,
    Bar,
    book_walk,
    price_path,
    read_bars,
)
from sim_platform.market.prepared.builder import build
from sim_platform.market.prepared.reader import PreparedMarket
from sim_platform.research import ResearchSession

SECOND = 1_000_000_000
DAILY = """Date,Open,High,Low,Close,Adj Close,Volume
2026-01-02,100.00,103.00,99.00,102.00,102.00,8000
2026-01-05,102.50,103.00,100.00,100.50,100.50,4000
2026-01-06,100.50,100.50,100.50,100.50,100.50,0
2026-01-07,101.00,104.00,101.00,104.00,104.00,6000
"""


class BarFileTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix='bar-source-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, text, name='bars.csv'):
        path = self.root / name
        path.write_text(text)
        return path

    def test_daily_rows_are_placed_in_the_regular_session_of_their_calendar_day(self):
        data = read_bars(self.write(DAILY))
        self.assertEqual((data.start_date, data.timeframe), ('2026-01-02', '1d'))
        # Monday the 5th is three calendar days after Friday the 2nd.
        self.assertEqual(
            [bar.start_ns for bar in data.bars[:2]], [DAILY_OPEN_NS, 3 * DAY_NS + DAILY_OPEN_NS]
        )
        self.assertEqual(calendar_date(data.start_date, data.bars[3].start_ns), '2026-01-07')
        self.assertEqual((data.bars[0].open, data.bars[0].volume), (1_000_000, 8000))

    def test_intraday_rows_keep_their_times_including_outside_regular_hours(self):
        text = (
            'timestamp,open,high,low,close,volume\n'
            '2026-03-03 04:01:00,10,10.02,9.99,10.01,300\n'
            '2026-03-03 04:00:00,10,10.01,9.98,10,500\n'
            '2026-03-02 19:59:00,9.9,9.95,9.9,9.95,200\n'
        )
        data = read_bars(self.write(text))  # newest-first is accepted
        self.assertEqual((data.timeframe, data.bar_ns), ('60s', 60 * SECOND))
        self.assertEqual(
            [bar.start_ns for bar in data.bars],
            [
                (19 * 3600 + 59 * 60) * SECOND,
                DAY_NS + 4 * 3600 * SECOND,
                DAY_NS + (4 * 3600 + 60) * SECOND,
            ],
        )

    def test_float_noise_is_rounded_to_cents_and_bad_files_are_rejected(self):
        data = read_bars(
            self.write(
                'date,open,high,low,close,volume\n2026-01-02,189.970001,190,189.5,189.9,10\n'
            )
        )
        self.assertEqual(data.bars[0].open, 1_899_700)
        for text in (
            'date,open,high,low,volume\n2026-01-02,1,2,1,5\n',
            'date,open,high,low,close,volume\n',
            'date,open,high,low,close,volume\n2026-01-02,5,4,3,5,10\n',
            'date,open,high,low,close,volume\n2026-01-02,1,2,1,1,5\n2026-01-02,1,2,1,1,5\n',
            'date,open,high,low,close,volume\nnot-a-date,1,2,1,1,5\n',
        ):
            with self.assertRaises(ContractError):
                read_bars(self.write(text))

    def test_a_rising_bar_visits_its_low_first_and_the_walk_moves_in_small_steps(self):
        rising = Bar(0, 1_000_000, 1_030_000, 990_000, 1_020_000, 100)
        falling = Bar(0, 1_020_000, 1_030_000, 990_000, 1_000_000, 100)
        self.assertEqual([p for _, p in price_path(rising, 60 * SECOND)][1:3], [990_000, 1_030_000])
        self.assertEqual(
            [p for _, p in price_path(falling, 60 * SECOND)][1:3], [1_030_000, 990_000]
        )
        walk = book_walk(rising, 60 * SECOND)
        times = [stamp for stamp, _, _ in walk]
        self.assertEqual(times, sorted(times))
        self.assertLess(times[-1], 60 * SECOND)
        self.assertEqual(
            [price for _, price, moment in walk if moment][0::3], [1_000_000, 1_020_000]
        )
        self.assertEqual(sum(moment for _, _, moment in walk), 4)
        largest = max(abs(b[1] - a[1]) for a, b in zip(walk, walk[1:]))
        self.assertLessEqual(largest, 400_000 // MAX_STEPS_PER_MOVE + 1)

    def test_prepared_bars_reproduce_the_file_and_skip_a_bar_without_volume(self):
        out = self.root / 'artifact'
        build(self.write(DAILY), out, symbol='TEST', depth=3)
        with PreparedMarket(out, verify=True) as market:
            manifest = market.manifest
            self.assertEqual(manifest['calendar']['start_date'], '2026-01-02')
            self.assertEqual(manifest['calendar']['days'], 4)
            first = market.snapshot_prefix(1)
            self.assertEqual(first.bids[0][0], 1_000_000 - HALF_SPREAD)
            self.assertEqual(first.asks[0][0], 1_000_000 + HALF_SPREAD)
            self.assertEqual(market.event(0).source_type, REQUOTE)
            trades = market.history.trades(manifest['frames'], manifest['last_ns'], limit=None)
            by_day = {}
            for trade in trades:
                by_day.setdefault(trade.timestamp_ns // DAY_NS, []).append(trade)
            # The 6th (day 4) had no volume, so nothing is quoted or printed for it.
            self.assertEqual(sorted(by_day), [0, 3, 5])
            day = by_day[0]
            self.assertEqual(
                (day[0].price, max(t.price for t in day), min(t.price for t in day), day[-1].price),
                (1_000_000, 1_030_000, 990_000, 1_020_000),
            )
            self.assertEqual(sum(t.quantity for t in day), 8000)
            requotes = [
                index
                for index in range(manifest['frames'])
                if market.event(index).source_type == REQUOTE
            ]
            self.assertEqual(len(requotes), 3)


class BarExperimentTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix='bar-experiment-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'bars.csv'
        self.source.write_text(DAILY)
        platform = Platform(self.root / 'accounts')
        self.addCleanup(platform.close)
        self.c = ResearchSession(platform, None)
        self.addCleanup(self.c.close)
        self.profile = self.c.create_profile('Researcher')
        self.account = self.c.create_account(self.profile, 'Account', 100_000_000)
        self.c.begin_prepare(self.source, symbol='TEST', start_mode='first_book')
        while self.c.preparation['status'] == 'preparing':
            self.c.prepare_tick()

    def test_orders_fill_near_the_shown_price_across_days_and_levels_are_renewed(self):
        view = self.c.start(self.profile, self.account, execution_mode='controlled')
        calendar = view['source']['prepared']['calendar']
        self.assertEqual(
            calendar_date(calendar['start_date'], view['market']['time_ns']), '2026-01-02'
        )
        shown = view['market']['asks'][0][0]
        self.c.order('buy', 'buy', 10, shown + 50_000)
        self.c.step()
        view = self.c.view()
        fill = next(e for e in view['events'] if e['kind'] == 'fill')
        # Filled on the next small step of the walk, not at the bar's low of 99.00.
        self.assertLessEqual(abs(fill['data']['price'] - shown), 1000)
        self.assertEqual(view['account']['positions'][0]['quantity'], 10)
        # Day one passes 101.00 on the way up; day four opens there again. A resting
        # sell at that price must still find buyers on the later day.
        while self.c.view()['market']['time_ns'] < 5 * DAY_NS:
            self.c.step()
        view = self.c.view()
        self.assertEqual(
            calendar_date(calendar['start_date'], view['market']['time_ns']), '2026-01-07'
        )
        self.c.order('sell', 'sell', 10, view['market']['bids'][0][0])
        self.c.step()
        self.c.step()
        view = self.c.view()
        self.assertEqual(view['account']['positions'], [])
        self.assertGreater(view['account']['cash'], 100_000_000)


if __name__ == '__main__':
    unittest.main()


class SessionGapTests(unittest.TestCase):
    """The experiment clock skips the closed market between two sessions."""

    HOUR = 3600 * SECOND

    def clock(self, origin):
        from sim_platform.experiments.scheduling.clock import ExperimentClock, SessionGaps

        self.real = 0
        gaps = SessionGaps(
            [(16 * self.HOUR, 33 * self.HOUR + self.HOUR // 2)],
            origin,
            grace_ns=5 * SECOND,
            lead_ns=2 * SECOND,
        )
        clock = ExperimentClock(origin, lambda: self.real, gaps)
        clock.resume()
        return clock

    def test_clock_runs_through_the_grace_period_then_lands_before_the_next_open(self):
        clock = self.clock(15 * self.HOUR)
        next_open = 33 * self.HOUR + self.HOUR // 2
        self.real = self.HOUR + 4 * SECOND
        self.assertEqual(clock.target_ns(), 16 * self.HOUR + 4 * SECOND)
        # Four real seconds remain: one of grace, the skip, then two of lead.
        self.assertEqual(clock.delay_ns(next_open), 3 * SECOND)
        self.real = self.HOUR + 5 * SECOND
        self.assertEqual(clock.target_ns(), next_open - 2 * SECOND)
        self.real = self.HOUR + 7 * SECOND
        self.assertEqual(clock.target_ns(), next_open)
        self.assertEqual(clock.delay_ns(next_open), 0)

    def test_pausing_inside_or_starting_inside_a_closed_stretch_is_consistent(self):
        clock = self.clock(15 * self.HOUR)
        self.real = self.HOUR + 6 * SECOND
        clock.pause()
        frozen = clock.target_ns()
        self.real += 100 * SECOND
        self.assertEqual(clock.target_ns(), frozen)
        clock.resume()
        self.real += SECOND
        self.assertEqual(clock.target_ns(), frozen + SECOND)
        # An experiment that starts while the market is closed starts at the skip's end.
        late = self.clock(20 * self.HOUR)
        self.assertEqual(late.target_ns(), 33 * self.HOUR + self.HOUR // 2 - 2 * SECOND)

    def test_a_recording_without_declared_gaps_gives_a_clock_that_never_skips(self):
        from sim_platform.experiments.scheduling.clock import ExperimentClock, session_gaps

        gaps = session_gaps(dict(symbol='TEST'), 0)
        self.assertFalse(gaps)
        self.real = 0
        clock = ExperimentClock(0, lambda: self.real, gaps)
        clock.resume()
        self.real = 40 * self.HOUR
        self.assertEqual(clock.target_ns(), 40 * self.HOUR)


class LiveMultiDayTests(unittest.TestCase):
    def test_a_live_run_continues_into_the_next_day_without_waiting_overnight(self):
        import time
        from unittest.mock import patch

        rows = ['timestamp,open,high,low,close,volume']
        for day in ('2026-03-02', '2026-03-03'):
            for second in range(2):
                rows.append(f'{day} 09:30:{second:02d},100.00,100.02,99.99,100.01,4000')
        temp = TemporaryDirectory(prefix='bar-live-')
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        source = root / 'two-days.csv'
        source.write_text('\n'.join(rows) + '\n')
        app = Platform(root / 'accounts')
        self.addCleanup(app.close)
        profile = app.create_profile('live')
        account = app.create_account(profile, 'cash', 100_000_000)
        with patch('sim_platform.experiments.scheduling.clock.GAP_GRACE_NS', SECOND // 5):
            run = app.open_experiment(profile, account, source=source, execution_mode='live')
        self.addCleanup(run.close)
        started = time.monotonic()
        run.play()
        days, status = set(), 'running'
        while status == 'running' and time.monotonic() - started < 30:
            view = run.view()
            days.add(view['market']['time_ns'] // DAY_NS)
            status = (view.get('execution') or {}).get('status', 'running')
            time.sleep(0.02)
        # About 24 hours of market time passed in a few real seconds.
        self.assertEqual(status, 'completed')
        self.assertEqual(days, {0, 1})
        self.assertLess(time.monotonic() - started, 20)
