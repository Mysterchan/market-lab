"""Prepare a market from OHLCV price bars in a CSV file.

A bar file has no order book, so one is synthesised. Each bar becomes four moments:
its open, then its low and high, then its close. One trade is printed at each moment,
and between moments the book walks from one price to the next in small steps. The
result is written in the same prepared layout as a native ITCH import, so playback
and execution need no special case.

The walk matters for fairness. A simulated order fills on the first book change after
it is accepted. Without the steps in between, an order sent at the open would fill at
the bar's next extreme, for example a buy at the day's low. With them, it fills within
about a tick of the price that was on screen when it was sent.

Everything about the book is an assumption, not data: the spread, the depth and the
size at each level are chosen here. The trade prints reproduce each bar's open, high,
low, close and volume exactly. Results from a bar file say how a strategy would have
done against these assumptions, not against the real order book of that day.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from sim_platform.domain.models import ContractError
from sim_platform.market.models import DAY_NS, MAX_TIME_NS, REQUOTE, MarketSnapshot

from .codec import EVENT, HAS_TRADE, PRINTABLE, encode_state

SECOND_NS = 1_000_000_000
# Prices are integers in USD ten-thousandths.
PRICE_SCALE = 10_000
# One cent, the smallest price step used for the synthetic book.
TICK = 100
# The synthetic best bid and best ask sit this far below and above the bar's price,
# which makes the default quoted spread two cents.
HALF_SPREAD = TICK
DEFAULT_SPREAD = 2 * HALF_SPREAD
DEFAULT_BOOK_DEPTH = 5
# The widest spread a user may choose for the synthetic book: ten dollars.
MAX_SPREAD = 100_000
MAX_BOOK_DEPTH = 100
# A daily bar is placed in the regular US session: open at 09:30, close at 16:00.
DAILY_OPEN_NS = 34_200 * SECOND_NS
DAILY_SESSION_NS = 23_400 * SECOND_NS
# Source types recorded for the synthetic events. The first book update of a bar is a
# requote: new liquidity, unrelated to the previous bar. Later updates within the bar
# are stored as an ITCH Replace (U), so shares taken earlier in the bar are not offered
# again. A trade print is stored as an ITCH non-cross Trade (P).
BAR_OPEN, BOOK_UPDATE, TRADE_PRINT = REQUOTE.encode('ascii'), b'U', b'P'
# The market is open for trading throughout a bar file.
TRADING_STATE, SYSTEM_EVENT = 'T', 'Q'
PROFILE = 'bar-derived-synthetic-book'
SUFFIXES = ('.csv',)
# Between two moments of a bar the price is moved in steps of about one tick, but in
# no more than this many steps, so a bar with a wide range stays a bounded number of events.
MAX_STEPS_PER_MOVE = 20
# A pause between two bars longer than this is recorded as a closed market, which a
# Live experiment skips instead of waiting through.
CLOSED_MARKET_NS = 30 * 60 * SECOND_NS
_CANCEL_CHECK_BARS = 2048
_MAX_BARS = 5_000_000

_TIME_COLUMNS = ('datetime', 'timestamp', 'date', 'time')
_DATE_FORMATS = ('%m/%d/%Y %H:%M:%S', '%m/%d/%Y %H:%M', '%m/%d/%Y', '%Y%m%d')


@dataclass(frozen=True)
class Bar:
    """One price bar. ``start_ns`` is market time; prices are USD ten-thousandths."""

    start_ns: int
    open: int
    high: int
    low: int
    close: int
    volume: int


@dataclass(frozen=True)
class BarFile:
    """The bars of a CSV file in time order, and how they map to the calendar.

    ``start_date`` is the ISO date of market day 0. ``bar_ns`` is the length of one
    bar. ``timeframe`` is ``1d`` for daily bars, otherwise the bar length in seconds
    followed by ``s``.
    """

    start_date: str
    timeframe: str
    bar_ns: int
    bars: Tuple[Bar, ...]


def is_bar_source(path: Path) -> bool:
    """Whether a source file should be prepared from bars instead of ITCH messages."""
    return path.suffix.lower() in SUFFIXES


def _moment(text: str, row: int) -> datetime:
    """Parse a date or date-time as written; a time zone suffix is dropped, not applied."""
    value = text.strip()
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        for layout in _DATE_FORMATS:
            try:
                parsed = datetime.strptime(value, layout)
                break
            except ValueError:
                continue
        else:
            raise ContractError(f'row {row}: unrecognised date or time {text!r}') from None
    return parsed.replace(tzinfo=None)


def _price(text: str, row: int, name: str) -> int:
    """Convert a decimal price to integer ten-thousandths, rounded to the nearest cent.

    Bar files often carry float noise such as 189.970001; quotes are in cents.
    """
    try:
        cents = (Decimal(text.strip()) * (PRICE_SCALE // TICK)).quantize(
            Decimal(1), ROUND_HALF_EVEN
        )
        value = cents * TICK
    except (InvalidOperation, ValueError):
        raise ContractError(f'row {row}: {name} is not a number') from None
    if not value.is_finite() or value <= 0:
        raise ContractError(f'row {row}: {name} must be a positive price')
    return int(value)


def _volume(text: str, row: int) -> int:
    try:
        value = Decimal(text.strip() or '0').quantize(Decimal(1), ROUND_HALF_EVEN)
    except (InvalidOperation, ValueError):
        raise ContractError(f'row {row}: volume is not a number') from None
    if not value.is_finite() or value < 0:
        raise ContractError(f'row {row}: volume must not be negative')
    return int(value)


def _columns(header: Sequence[str]) -> Dict[str, int]:
    """Locate the needed columns by name, ignoring case and surrounding spaces."""
    names = {name.strip().lower(): index for index, name in enumerate(header)}
    found = {name: names[name] for name in ('open', 'high', 'low', 'close') if name in names}
    if len(found) != 4:
        raise ContractError('bar file needs open, high, low and close columns in its header')
    for name in ('volume', 'vol'):
        if name in names:
            found['volume'] = names[name]
            break
    else:
        raise ContractError('bar file needs a volume column')
    if 'date' in names and 'time' in names:
        found['date'], found['time'] = names['date'], names['time']
        return found
    for name in _TIME_COLUMNS:
        if name in names:
            found['moment'] = names[name]
            return found
    raise ContractError('bar file needs a date, datetime or timestamp column')


def bar_file_span(path: Path) -> Tuple[str, str]:
    """The first and last date of a bar file, read from its first and last rows only.

    Used to describe a file in the library without reading every bar; rows may be
    oldest-first or newest-first. Raises ContractError if either row cannot be read.
    """

    def moment(cells: Sequence[str], columns: Dict[str, int]) -> datetime:
        try:
            stamp = (
                cells[columns['moment']]
                if 'moment' in columns
                else cells[columns['date']] + ' ' + cells[columns['time']]
            )
        except IndexError:
            raise ContractError('bar file row is missing columns') from None
        return _moment(stamp, 0)

    with path.open(newline='', encoding='utf-8-sig') as stream:
        reader = csv.reader(stream)
        header = next(reader, None)
        if header is None:
            raise ContractError('bar file is empty')
        columns = _columns(header)
        first = next((cells for cells in reader if any(c.strip() for c in cells)), None)
    if first is None:
        raise ContractError('bar file has no bars')
    with path.open('rb') as binary:
        binary.seek(0, 2)
        binary.seek(max(0, binary.tell() - 4096))
        tail = binary.read().decode('utf-8', errors='replace').splitlines()
    lines = [line for line in tail if line.strip()]
    last = next(csv.reader([lines[-1]])) if lines else first
    ends = sorted((moment(first, columns), moment(last, columns)))
    return ends[0].date().isoformat(), ends[1].date().isoformat()


def _rows(path: Path) -> List[Tuple[datetime, int, int, int, int, int]]:
    """Read and validate every row as (moment, open, high, low, close, volume)."""
    rows = []
    with path.open(newline='', encoding='utf-8-sig') as stream:
        reader = csv.reader(stream)
        header = next(reader, None)
        if header is None:
            raise ContractError('bar file is empty')
        columns = _columns(header)
        for number, cells in enumerate(reader, 2):
            if not any(cell.strip() for cell in cells):
                continue
            try:
                stamp = (
                    cells[columns['moment']]
                    if 'moment' in columns
                    else cells[columns['date']] + ' ' + cells[columns['time']]
                )
                values = [cells[columns[name]] for name in ('open', 'high', 'low', 'close')]
                volume = cells[columns['volume']]
            except IndexError:
                raise ContractError(f'row {number}: missing columns') from None
            opened, high, low, closed = (
                _price(text, number, name)
                for text, name in zip(values, ('open', 'high', 'low', 'close'))
            )
            if high < max(opened, closed) or low > min(opened, closed):
                raise ContractError(f'row {number}: high/low do not contain open and close')
            rows.append(
                (_moment(stamp, number), opened, high, low, closed, _volume(volume, number))
            )
            if len(rows) > _MAX_BARS:
                raise ContractError('bar file has too many rows')
    if not rows:
        raise ContractError('bar file has no bars')
    return rows


def read_bars(path: Path, origin: Optional[str] = None) -> BarFile:
    """Read a CSV of OHLCV bars and place every bar on the market timeline.

    Market day 0 is the file's first date, or ``origin`` ('YYYY-MM-DD', no later than
    that date) when several recordings must share one timeline.

    The header must name ``open``, ``high``, ``low``, ``close``, ``volume`` and either
    a ``date``/``datetime``/``timestamp`` column or separate ``date`` and ``time``
    columns. Rows may be oldest-first or newest-first. Times are used as written, in
    the exchange's local time. A file whose rows carry no time of day is daily.

    Raises:
        ContractError: If the header, a value, or the order of the rows is invalid.
    """
    rows = _rows(path)
    if len(rows) > 1 and rows[0][0] > rows[-1][0]:
        rows.reverse()
    moments = [row[0] for row in rows]
    if any(later <= earlier for earlier, later in zip(moments, moments[1:])):
        raise ContractError('bar times must be distinct and in one consistent order')
    first: date = moments[0].date()
    if origin is not None:
        shared = date.fromisoformat(origin)
        if shared > first:
            raise ContractError('the shared first date is after this file\'s first bar')
        first = shared
    daily = all(moment.time() == datetime.min.time() for moment in moments)
    if daily:
        bar_ns, timeframe = DAILY_SESSION_NS, '1d'
    else:
        gaps = [
            int((later - earlier).total_seconds())
            for earlier, later in zip(moments, moments[1:])
            if later.date() == earlier.date()
        ]
        seconds = min(gaps) if gaps else 60
        if seconds < 1:
            raise ContractError('bars must be at least one second apart')
        bar_ns, timeframe = seconds * SECOND_NS, f'{seconds}s'
    bars = []
    for moment, opened, high, low, closed, volume in rows:
        day = (moment.date() - first).days
        within = (
            DAILY_OPEN_NS
            if daily
            else int(
                (moment - datetime.combine(moment.date(), datetime.min.time())).total_seconds()
            )
            * SECOND_NS
        )
        start = day * DAY_NS + within
        if start + bar_ns >= MAX_TIME_NS:
            raise ContractError('bar file spans more than the supported 100 years')
        bars.append(Bar(start, opened, high, low, closed, volume))
    return BarFile(first.isoformat(), timeframe, bar_ns, tuple(bars))


def price_path(bar: Bar, bar_ns: int) -> Tuple[Tuple[int, int], ...]:
    """The four (time, price) moments a bar is expanded into.

    A bar says nothing about the order of its high and low. The usual convention is
    assumed: a bar that closes up visits its low first, a bar that closes down visits
    its high first. The close is placed just before the bar ends, so it stays inside
    the bar's own interval.
    """
    rising = bar.close >= bar.open
    middle = (bar.low, bar.high) if rising else (bar.high, bar.low)
    before_end = bar_ns - max(1, min(SECOND_NS, bar_ns // 60))
    offsets = (0, bar_ns // 3, 2 * bar_ns // 3, before_end)
    prices = (bar.open, middle[0], middle[1], bar.close)
    return tuple((bar.start_ns + offset, price) for offset, price in zip(offsets, prices))


def book_walk(bar: Bar, bar_ns: int) -> List[Tuple[int, int, bool]]:
    """Every book update of a bar as (time, price, is one of the four moments).

    Between consecutive moments the price moves in a straight line, one update per
    tick of movement, limited to MAX_STEPS_PER_MOVE updates and to one per nanosecond.
    """
    moments = price_path(bar, bar_ns)
    walk = [(moments[0][0], moments[0][1], True)]
    for (start, opened), (end, closed) in zip(moments, moments[1:]):
        steps = max(1, min(MAX_STEPS_PER_MOVE, abs(closed - opened) // TICK, end - start))
        for step in range(1, steps):
            walk.append(
                (
                    start + (end - start) * step // steps,
                    # Whole ticks from the starting price, so every quote stays on the cent grid.
                    opened + (closed - opened) * step // steps // TICK * TICK,
                    False,
                )
            )
        walk.append((end, closed, True))
    return walk


def validate_book(spread: int, depth: int) -> None:
    """Check a user's synthetic-book choice: a whole number of cents and a level count."""
    if type(spread) is not int or not TICK <= spread <= MAX_SPREAD or spread % TICK:
        raise ContractError('spread must be a whole number of cents from $0.01 to $10.00')
    if type(depth) is not int or not 1 <= depth <= MAX_BOOK_DEPTH:
        raise ContractError(f'book depth must be 1..{MAX_BOOK_DEPTH} levels per side')


def book_options(value: Any) -> Optional[Dict[str, int]]:
    """Normalise a caller's synthetic-book choice to {'spread', 'depth'}, or None.

    ``spread`` is the best-ask minus best-bid distance in integer money (200 is two
    cents) and ``depth`` the levels shown per side; a missing key takes its default.
    """
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) - {'spread', 'depth'}:
        raise ContractError('book options accept only spread and depth')
    book = dict(
        spread=value.get('spread', DEFAULT_SPREAD), depth=value.get('depth', DEFAULT_BOOK_DEPTH)
    )
    validate_book(book['spread'], book['depth'])
    return book


def synthetic_levels(
    price: int, size: int, depth: int, spread: int = DEFAULT_SPREAD
) -> Tuple[Tuple[Tuple[int, int], ...], Tuple[Tuple[int, int], ...]]:
    """Bid and ask levels around ``price``, best first, each showing ``size`` shares.

    The best bid is half the spread below ``price``, rounded down to a whole cent, and
    the best ask is the spread above the best bid. Further levels are one cent apart.
    """
    best_bid = price - (spread // TICK // 2) * TICK
    best_ask = best_bid + spread
    bids = tuple(
        (best_bid - level * TICK, size) for level in range(depth) if best_bid - level * TICK > 0
    )
    asks = tuple((best_ask + level * TICK, size) for level in range(depth))
    return bids, asks


def export_bars(
    source: Path,
    stage: Path,
    symbol: str,
    depth: int,
    cancelled: Callable[[], bool],
    progress: Callable[[int], Any],
    spread: int = DEFAULT_SPREAD,
    origin: Optional[str] = None,
) -> Dict[str, Any]:
    """Write ``events.bin`` and ``states.bin`` for a bar file and return its summary.

    ``depth`` is the number of levels shown on each side and ``spread`` the distance
    between the best bid and the best ask, in integer money; both are recorded in the
    manifest's ``calendar.synthetic_book``.

    Every step of a bar's walk writes a book update; each of its four moments also
    writes a trade print, and the four prints add up to the bar's volume.

    The size shown at each level is the bar's volume divided by the number of price
    levels the walk shows, so over a whole bar roughly the bar's volume is on offer on
    each side: a simulated order cannot trade much more than really traded. A bar
    without volume is skipped: nothing traded, so no liquidity is offered and the
    previous book stays in place.
    """
    from .builder import PreparationCancelled

    data = read_bars(source, origin)
    empty = MarketSnapshot(symbol, 0, 0, 'playing', 'ordered_unverified')
    frames = versions = 0
    first_ns: Optional[int] = None
    last_ns = 0
    with (stage / 'events.bin').open('wb') as events, (stage / 'states.bin').open('wb') as states:
        states.write(encode_state(empty, depth))
        for index, bar in enumerate(data.bars):
            if index % _CANCEL_CHECK_BARS == 0:
                if cancelled():
                    raise PreparationCancelled('market preparation cancelled')
                progress(frames)
            if not bar.volume:
                continue
            walk = book_walk(bar, data.bar_ns)
            size = max(1, bar.volume // (len(walk) + depth))
            prints = [bar.volume // 4] * 4
            prints[3] += bar.volume % 4
            update_type = BAR_OPEN
            for stamp, price, is_moment in walk:
                bids, asks = synthetic_levels(price, size, depth, spread)
                versions += 1
                frames += 1
                book = MarketSnapshot(
                    symbol,
                    stamp,
                    frames,
                    'playing',
                    'ordered_unverified',
                    bids,
                    asks,
                    len(bids),
                    len(asks),
                    stamp,
                    TRADING_STATE,
                    SYSTEM_EVENT,
                )
                states.write(encode_state(book, depth))
                events.write(EVENT.pack(stamp, frames, versions, b'M', update_type, 0, 0, 0, 0, 0))
                update_type = BOOK_UPDATE
                quantity = prints.pop(0) if is_moment else 0
                if quantity:
                    frames += 1
                    # The match ID only has to be unique; the event number is.
                    events.write(
                        EVENT.pack(
                            stamp,
                            frames,
                            versions,
                            b'M',
                            TRADE_PRINT,
                            frames,
                            price,
                            quantity,
                            HAS_TRADE | PRINTABLE,
                            0,
                        )
                    )
                first_ns = stamp if first_ns is None else first_ns
                last_ns = stamp
    if first_ns is None:
        raise ContractError('bar file has no bar with volume, so there is nothing to trade')
    progress(frames)
    days = {bar.start_ns // DAY_NS for bar in data.bars}
    traded = [bar for bar in data.bars if bar.volume]
    gaps = [
        [earlier.start_ns + data.bar_ns, later.start_ns]
        for earlier, later in zip(traded, traded[1:])
        if later.start_ns - (earlier.start_ns + data.bar_ns) > CLOSED_MARKET_NS
    ]
    return dict(
        frames=frames,
        source_frames=frames,
        states=versions + 1,
        first_ns=first_ns,
        last_ns=last_ns,
        read_ns=0,
        apply_ns=0,
        encode_write_ns=0,
        profile=PROFILE,
        calendar=dict(
            start_date=data.start_date,
            timeframe=data.timeframe,
            bar_seconds=data.bar_ns // SECOND_NS,
            bars=len(data.bars),
            days=len(days),
            # Market times between sessions, each as [last bar's end, next bar's start].
            gaps=gaps,
            synthetic_book=dict(
                spread=spread,
                tick=TICK,
                depth=depth,
                max_steps_per_move=MAX_STEPS_PER_MOVE,
            ),
        ),
    )
