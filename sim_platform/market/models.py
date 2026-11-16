"""Market contracts are independent of account snapshots and simulated fills."""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Dict, Optional, Tuple


# Nanoseconds in one calendar day.
DAY_NS = 86_400_000_000_000
# Market time is counted in nanoseconds from midnight of a recording's first day, in
# the exchange's local time. A one-day recording stays below DAY_NS. A multi-day
# recording continues counting, so day N starts at N * DAY_NS; nights, weekends and
# holidays are simply stretches with no events. One hundred years is the upper bound.
MAX_TIME_NS = 36_525 * DAY_NS


# Source type of a book update that replaces the whole displayed book with new quotes.
# ITCH has no such message; a recording prepared from price bars uses it at the start
# of every bar, where the liquidity on offer is new and unrelated to the bar before.
REQUOTE = 'Z'


class MarketError(ValueError):
    """A market data request or source is invalid."""


class FutureDataError(MarketError):
    """A query asked for data later than the market's current time."""


class DataUnavailable(MarketError):
    """The requested data is not loaded, not prepared, or no longer retained."""


def time_ns(value: object) -> int:
    """Validate and return a market time in nanoseconds since the first day's midnight."""
    if type(value) is not int or not 0 <= value < MAX_TIME_NS:
        raise MarketError('time must be integer nanoseconds since the first day\'s midnight')
    return value


def day_index(value: int) -> int:
    """Which day of the recording a market time falls on; the first day is 0."""
    return value // DAY_NS


def calendar_date(start_date: str, value: int) -> str:
    """The ISO calendar date of a market time, given the recording's first date."""
    from datetime import date, timedelta

    return (date.fromisoformat(start_date) + timedelta(days=day_index(value))).isoformat()


@dataclass(frozen=True)
class FeedRecord:
    """One raw message of a sequenced feed: its transport sequence and payload bytes."""

    sequence: int
    payload: bytes


@dataclass(frozen=True)
class NewsRecord:
    """A publication that becomes visible to the experiment at ``available_time_ns``."""

    record_id: str
    available_time_ns: int
    period: str
    headline: str
    revision: int = 1
    provenance: str = 'user_declared_fixture'


@dataclass(frozen=True)
class MarketTrade:
    """One historical trade. ``price`` is in USD ten-thousandths.

    ``printable`` is false for trades the exchange excludes from volume and last-sale
    figures. ``kind`` is the ITCH message type that reported the trade.
    """

    sequence: int
    timestamp_ns: int
    match_id: int
    price: int
    quantity: int
    printable: bool
    kind: str


@dataclass(frozen=True)
class MarketSnapshot:
    """The market for one symbol at one moment.

    ``bids`` and ``asks`` are the displayed book, best price first, as
    (price in USD ten-thousandths, displayed shares). ``bid_levels`` and
    ``ask_levels`` count all levels in the full book, which may exceed the depth kept
    here. ``book_time_ns`` is when the book last changed, or None before the first
    quote. ``sequence`` counts market events applied so far.
    """

    symbol: str
    time_ns: int
    sequence: int
    status: str
    integrity: str
    bids: Tuple[Tuple[int, int], ...] = ()
    asks: Tuple[Tuple[int, int], ...] = ()
    bid_levels: int = 0
    ask_levels: int = 0
    book_time_ns: Optional[int] = None
    trading_state: str = 'unknown'
    system_event: str = 'unknown'
    error: Optional[str] = None

    @property
    def day(self) -> int:
        """Which day of the recording this snapshot falls on; the first day is 0."""
        return day_index(self.time_ns)

    @property
    def time_of_day(self) -> str:
        """The snapshot's time within its day as HH:MM:SS.nnnnnnnnn."""
        seconds, nanos = divmod(self.time_ns % DAY_NS, 1_000_000_000)
        hours, seconds = divmod(seconds, 3600)
        minutes, seconds = divmod(seconds, 60)
        return f'{hours:02d}:{minutes:02d}:{seconds:02d}.{nanos:09d}'


class MarketView(MarketSnapshot):
    """The current event's book, plus every symbol's book at the same moment.

    Used by experiments on several symbols. It is a MarketSnapshot, so everything that
    reads one book keeps working; ``books`` maps each symbol to its own snapshot, stamped
    with the same time and merged sequence. ``books`` is not a dataclass field, so the
    JSON form (and every recorded fingerprint) is that of the current book alone.
    """

    @classmethod
    def of(cls, primary: MarketSnapshot, books: Dict[str, MarketSnapshot]) -> MarketView:
        view = cls(**{f.name: getattr(primary, f.name) for f in fields(MarketSnapshot)})
        object.__setattr__(view, '_books', dict(books))
        return view

    @property
    def books(self) -> Dict[str, MarketSnapshot]:
        return dict(self.__dict__.get('_books') or {self.symbol: self})


def snapshot_universe(market: MarketSnapshot) -> list:
    """The symbols a snapshot covers: every book of a MarketView, else its own symbol."""
    if isinstance(market, MarketView):
        return list(market.books)
    return [market.symbol]


def book_for(market: MarketSnapshot, symbol: str) -> MarketSnapshot:
    """The snapshot of ``symbol``: from a MarketView's books, or the snapshot itself."""
    if isinstance(market, MarketView):
        book = market.books.get(symbol)
        if book is not None:
            return book
        # The current book as a plain snapshot: a MarketView carries every symbol's
        # books and is not a value that crosses process boundaries.
        return MarketSnapshot(**{f.name: getattr(market, f.name) for f in fields(MarketSnapshot)})
    return market


@dataclass(frozen=True)
class MarketEvent:
    """One step of market playback and the snapshot after applying it.

    ``kind`` is ``market`` for a source message about this symbol, ``clock`` for a
    message about another symbol that only moves time forward, ``news`` for a
    publication, ``status`` when playback state changes (including completion), and
    ``integrity`` when the source fails. ``source_type`` is the ITCH message type of
    a ``market`` or ``clock`` event.
    """

    kind: str
    time_ns: int
    sequence: int
    source_type: str
    snapshot: MarketSnapshot
    trade: Optional[MarketTrade] = None
    news: Optional[NewsRecord] = None
    message: Optional[str] = None
    broken_match_id: Optional[int] = None


@dataclass(frozen=True)
class PriceVolumeBar:
    """Printable trades aggregated over [start_ns, end_ns).

    Prices are in USD ten-thousandths. ``complete`` is false while the bar's interval
    extends past ``observed_through_ns``, the time the data was read at.
    """

    start_ns: int
    end_ns: int
    observed_through_ns: int
    complete: bool
    open: int
    high: int
    low: int
    close: int
    volume: int
    count: int
    notional: int
