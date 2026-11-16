# Price-bar CSV input

A CSV file of OHLCV price bars can be used as a recording instead of an ITCH file.
Add it under Market data, or pass its path wherever a recording path is accepted. The
file name must end in `.csv`.

A bar file contains no order book. The platform builds one from assumptions, described
below. Results against a bar file show how a strategy would have done against those
assumptions. They are not results against the real order book of that day.

## Accepted files

The first row is a header. Column names are matched without regard to case.

| Column | Required | Notes |
| --- | --- | --- |
| `date`, `datetime` or `timestamp` | Yes | ISO format, `MM/DD/YYYY` or `YYYYMMDD`. Separate `date` and `time` columns also work |
| `open`, `high`, `low`, `close` | Yes | Decimal prices, rounded to the nearest cent |
| `volume` or `vol` | Yes | Shares traded in the bar |
| Any other column | No | Ignored, including `Adj Close` |

Rows may be oldest first or newest first. Times must be distinct. A time zone suffix is
dropped, not applied: write times in the exchange's local time.

A file whose rows have no time of day is read as daily bars. Any other file is read as
intraday bars, and the bar length is the smallest gap between two rows of the same day.
Intraday rows may fall at any time of day, so pre-market, after-hours and overnight
sessions are kept as written.

## Time and dates

Market time counts nanoseconds from midnight of the file's first day. Later days keep
counting, so the fourth calendar day starts at three days' worth of nanoseconds. Nights,
weekends and holidays are stretches with no events. The prepared recording stores the
first date, and the Desktop shows dates with it.

A daily bar is placed in the regular session of its date: its open at 09:30 and its close
just before 16:00.

## Closed markets in Live mode

A pause of more than 30 minutes between two bars is recorded as a closed market. In Live
and Agent experiments the clock does not wait through it. After the last bar of a session
the clock runs on for a short grace period, so work already in progress can finish, and
then moves to two seconds before the next session's first bar. The grace period is five
seconds, plus the work timeout when the experiment has a Rule or Agent decision maker.

Orders that are still open stay open across the closed market and can fill in the next
session. Controlled mode has no clock, so nothing is skipped there: stepping simply moves
to the next event.

An Agent's schedule is measured on market time, so the skip makes any pending interval
due at once. The Agent then wakes once, shortly before the next session opens, not once
for every interval that fell inside the closed market; later wakes follow the interval
from there. Work that starts during the grace period and is still running at the skip
finishes after it: its later tool calls and orders carry times from just before the next
open, and an order sent then fills against the next session's book. Replay of such a run
uses those recorded times and verifies like any other run.

In the Desktop, an Agent experiment's end time is a time of day. On a multi-day recording
it applies to the last day, so the Agent runs through every day.

## The synthetic book

Each bar becomes four moments: its open, then its low and high, then its close. A bar that
closes at or above its open is assumed to reach its low first; a bar that closes below its
open is assumed to reach its high first. One trade is printed at each moment, and the four
prints add up to the bar's volume, so the bars a strategy reads match the file.

Between moments the price moves in steps of about one cent, with at most 20 steps per move.
At every step the book shows:

- a best bid half the spread below the price, rounded down to a whole cent, and a best
  ask the spread above the best bid. The default spread is two cents: one cent each side;
- further levels one cent apart, up to the selected depth (five by default);
- the same size at every level: the bar's volume divided by the number of levels shown
  during the bar. Over a whole bar, roughly the bar's volume is on offer on each side.

A simulated order fills on the first book change after it is accepted, as with ITCH data.
Because of the small steps, an order sent while a price is shown fills within a few cents
of it, not at the bar's next extreme.

### Choosing the spread and depth

The spread and depth are assumptions, so you choose them when you add the file. A wider
spread makes every round trip cost more; more depth lets a large order reach further
from the touch, and spreads each bar's volume over more levels.

| Where | How |
| --- | --- |
| Desktop | Market data → Add recording → Synthetic order book (shown for a `.csv` file). The choice is saved with the recording and used for every experiment on it |
| Python | `open_experiment(..., book={'spread': 500, 'depth': 3})`, with the spread in integer money (500 is $0.05). `open_datasets().add(path, book=...)` saves a choice with the recording |
| Command line | `evaluate` or `compare` with `--bar-spread 0.05 --book-depth 3` |

The spread must be a whole number of cents from $0.01 to $10.00, and the depth 1 to 100
levels per side. The chosen values are written to the prepared manifest as
`calendar.synthetic_book` (`spread`, `tick`, `depth`, `max_steps_per_move`), recorded with
every run, and shown on the recording's details, the experiment workspace and the saved
result. A file prepared with one choice is prepared again, separately, for another.

Each bar starts with new liquidity. Shares a simulated order takes are not offered again
within the same bar, but the next bar offers its own. A bar with zero volume offers nothing
and leaves the previous book in place.

## Limits

- The order of the high and low inside a bar is assumed. With wide daily bars this
  assumption matters; intraday bars reduce it.
- The spread and depth are chosen assumptions, not measurements, and stay the same for
  the whole file.
- Live and Agent experiments run at real speed while the market is open. A daily bar
  therefore takes a whole real session; use Controlled mode for daily files.

## Custom start on a later day

A custom start picks a date as well as a time of day. In the Desktop, enter the start date
next to the start time (empty means the first day). In Python, pass
`settings=dict(start_mode='custom', start_date='2026-01-07', start_time_ns=...)`, where
`start_time_ns` is then the time of day. The date must fall within the recording. An ITCH
file has no dates, so a date is refused for it; give `start_time_ns` alone.

## Replay

`Platform.replay_run()` verifies a finished Controlled run made from a bar file, as it does
for ITCH. It does not read the run's prepared artifact. It prepares the original file again
with the recorded symbol, spread and depth, checks that the result has the same number of
events and the same time coverage, starts at the run's recorded position and applies the
recorded decisions. The original file must still be at its recorded path with the same
size and modification time; otherwise Replay stops with an error. Agent and Live runs are
verified from their operation log with `replay_prepared_operations()` instead.

[Section index](README.md) · [All documentation](../README.md)
