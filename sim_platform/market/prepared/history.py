"""Immutable trade/correction and minute-bar versions, built once at import.

Queries binary-search time/prefix indexes. Corrections affect only observations
at or after their publication, never earlier historical views. No book replay.
"""

from __future__ import annotations

from collections import defaultdict
import mmap
from pathlib import Path
from struct import Struct
from typing import Any, Callable, DefaultDict, Dict, List, Optional, Sequence, Set, Tuple

from sim_platform.domain.models import ContractError, integer
from sim_platform.market.models import (
    MAX_TIME_NS,
    DataUnavailable,
    MarketTrade,
    PriceVolumeBar,
)
from sim_platform.market.window import BAR_LIMIT, TRADE_LIMIT

from .codec import BROKEN, EVENT, HAS_TRADE, PRINTABLE, STATE, state_size

# An "ordinal" below is the 1-based position of an event in the prepared timeline.
# A trade or bar version is visible to a query once the query's ordinal reaches it.

INTERVAL = 60_000_000_000
SECOND_NS = 1_000_000_000
TRADE = Struct('>QQQQQ?cQ')  # prepared ordinal,time,match,price,size,printable,type,broken ordinal
BAR = Struct('>10Q')  # available ordinal/time, bucket + OHLC/volume/count/notional
BUCKET = Struct('>QQQ')  # minute, first version offset (records), version count
# Stored as a trade's broken ordinal while the trade stands.
NEVER = 2**64 - 1
# ITCH messages that report a trade: Executed (E), Executed with price (C),
# non-cross Trade (P) and Cross trade (Q).
TRADE_MESSAGES = (b'E', b'C', b'P', b'Q')
_CANCEL_CHECK_EVENTS = 8192

# Open, high, low, close, volume, trade count, notional.
Aggregate = Tuple[int, int, int, int, int, int, int]


def _aggregate(rows: Sequence[Sequence[Any]]) -> Aggregate:
    """Aggregate trade rows into one bar; an empty list gives an all-zero bar."""
    if not rows:
        return (0,) * 7
    prices = [r[3] for r in rows]
    return (
        prices[0],
        max(prices),
        min(prices),
        prices[-1],
        sum(r[4] for r in rows),
        len(rows),
        sum(r[3] * r[4] for r in rows),
    )


def build_history(
    stage: Path, frames: int, depth: int, cancelled: Callable[[], bool]
) -> Dict[str, Any]:
    """Write immutable indexes; memory proportional to selected trades/bar revisions.

    Input events are already ordered and structurally verified by the importer.
    Empty history uses zero-length files, not mmap of an empty file.
    """
    with (stage / 'states.bin').open('rb') as states_source:
        states = mmap.mmap(states_source.fileno(), 0, access=mmap.ACCESS_READ)
    try:
        return _build_history(stage, frames, depth, cancelled, states)
    finally:
        states.close()


def _build_history(
    stage: Path, frames: int, depth: int, cancelled: Callable[[], bool], states: Any
) -> Dict[str, Any]:
    """Scan every event once, then write the trade file and the bar version files.

    Each change to a minute's bar appends a new version stamped with the ordinal at
    which it became true, so a later query can read the bar as it stood at any point.
    """
    # Trade rows in TRADE layout; mutable until written, because a break edits them.
    trades: List[List[Any]] = []
    # Indexes into ``trades`` for each match ID.
    matches: Dict[int, List[int]] = {}
    printable_matches: Set[int] = set()
    first_book_ns: Optional[int] = None
    # Events per second of market time, to report the busiest second.
    density: DefaultDict[int, int] = defaultdict(int)
    # Printable trades of each minute, and that minute's packed bar versions.
    members: DefaultDict[int, List[List[Any]]] = defaultdict(list)
    versions: DefaultDict[int, bytearray] = defaultdict(bytearray)
    with (stage / 'events.bin').open('rb') as source:
        with mmap.mmap(source.fileno(), 0, access=mmap.ACCESS_READ) as events:
            for index in range(frames):
                if index % _CANCEL_CHECK_EVENTS == 0 and cancelled():
                    from .builder import PreparationCancelled

                    raise PreparationCancelled('history preparation cancelled')
                row = EVENT.unpack_from(events, index * EVENT.size)
                stamp, _, _, _, kind, match, price, quantity, flags, broken = row
                if first_book_ns is None:
                    state = STATE.unpack_from(states, row[2] * state_size(depth))
                    # The last two fields count retained bid and ask levels.
                    if state[-2] and state[-1]:
                        first_book_ns = stamp
                density[stamp // SECOND_NS] += 1
                ordinal = index + 1
                bucket = None
                if flags & HAS_TRADE:
                    if flags & PRINTABLE and quantity:
                        if match in printable_matches:
                            raise ContractError('duplicate printable prepared trade match number')
                        printable_matches.add(match)
                    matches.setdefault(match, []).append(len(trades))
                    trade = [
                        ordinal,
                        stamp,
                        match,
                        price,
                        quantity,
                        bool(flags & PRINTABLE),
                        kind,
                        NEVER,
                    ]
                    trades.append(trade)
                    if trade[5] and quantity:
                        bucket = stamp // INTERVAL * INTERVAL
                        members[bucket].append(trade)
                if flags & BROKEN:
                    if broken not in matches or any(trades[i][7] != NEVER for i in matches[broken]):
                        raise ContractError('unknown or duplicate prepared broken trade')
                    for trade_index in matches[broken]:
                        trade = trades[trade_index]
                        trade[7] = ordinal
                        if trade[5] and trade[4]:
                            bucket = trade[1] // INTERVAL * INTERVAL
                if bucket is not None:
                    # Common append O(1); a correction recomputes only its affected minute.
                    previous = versions[bucket]
                    if flags & BROKEN:
                        aggregate = _aggregate([t for t in members[bucket] if t[7] == NEVER])
                    elif previous:
                        old = BAR.unpack_from(previous, len(previous) - BAR.size)[3:]
                        aggregate = (
                            old[0] or price,
                            max(old[1], price),
                            min(old[2], price) if old[2] else price,
                            price,
                            old[4] + quantity,
                            old[5] + 1,
                            old[6] + price * quantity,
                        )
                    else:
                        aggregate = (price, price, price, price, quantity, 1, price * quantity)
                    previous.extend(BAR.pack(ordinal, stamp, bucket, *aggregate))
    with (stage / 'trades.bin').open('wb') as output:
        for trade_row in trades:
            output.write(TRADE.pack(*trade_row))
    offset = 0
    with (
        (stage / 'bar-versions.bin').open('wb') as output,
        (stage / 'bar-index.bin').open('wb') as directory,
    ):
        for bucket, values in sorted(versions.items()):
            count = len(values) // BAR.size
            directory.write(BUCKET.pack(bucket, offset, count))
            output.write(values)
            offset += count
    return dict(
        trades=len(trades),
        bar_versions=offset,
        buckets=len(versions),
        interval_ns=INTERVAL,
        first_book_ns=first_book_ns,
        peak_events_per_second=max(density.values()),
        peak_second_ns=max(density, key=lambda second: density[second]) * SECOND_NS,
    )


class PreparedHistory:
    """Borrow validated immutable mappings from a reader; caller fixes cutoff/prefix."""

    def __init__(self, trades: Any, bars: Any, buckets: Any, metadata: Dict[str, Any]) -> None:
        self._trades, self._bars, self._buckets = trades, bars, buckets
        self._meta = metadata

    def validate(self, frames: int, cancelled: Callable[[], bool] = lambda: False) -> None:
        """Check ordering and field ranges of every trade, bar directory entry and version."""
        previous = 0
        for index in range(self._meta['trades']):
            if index % _CANCEL_CHECK_EVENTS == 0 and cancelled():
                from .builder import PreparationCancelled

                raise PreparationCancelled('history validation cancelled')
            row = self._trade(index)
            if not previous < row[0] <= frames or (
                row[7] != NEVER and not row[0] < row[7] <= frames
            ):
                raise ContractError('invalid prepared trade ordering/correction')
            if row[1] >= MAX_TIME_NS or row[6] not in TRADE_MESSAGES:
                raise ContractError('invalid prepared trade fields')
            previous = row[0]
        total, previous_bucket = 0, -1
        for index in range(self._meta['buckets']):
            bucket, offset, count = BUCKET.unpack_from(self._buckets, index * BUCKET.size)
            if bucket <= previous_bucket or bucket % INTERVAL or offset != total or not count:
                raise ContractError('invalid prepared bar directory')
            if offset + count > self._meta['bar_versions']:
                raise ContractError('prepared bar versions exceed file')
            prior = 0
            for version in range(count):
                row = BAR.unpack_from(self._bars, (offset + version) * BAR.size)
                if not prior < row[0] <= frames or row[2] != bucket or row[1] < bucket:
                    raise ContractError('invalid prepared bar availability')
                if row[8] and (not row[3] or row[4] < row[5] or row[7] == 0):
                    raise ContractError('invalid prepared bar values')
                prior = row[0]
            total += count
            previous_bucket = bucket
        if total != self._meta['bar_versions']:
            raise ContractError('prepared history count mismatch')

    def _trade(self, index: int) -> Tuple[Any, ...]:
        return TRADE.unpack_from(self._trades, index * TRADE.size)

    def _upper(self, ordinal: int) -> int:
        """Number of trades that had occurred by ``ordinal``."""
        low, high = 0, self._meta['trades']
        while low < high:
            mid = (low + high) // 2
            if self._trade(mid)[0] <= ordinal:
                low = mid + 1
            else:
                high = mid
        return low

    def trades(
        self,
        ordinal: int,
        cutoff: int,
        *,
        start_ns: int = 0,
        limit: Optional[int] = TRADE_LIMIT,
        include_nonprintable: bool = False,
    ) -> Tuple[MarketTrade, ...]:
        """Return the most recent trades visible at ``ordinal``, oldest first.

        A trade broken at or before ``ordinal`` is left out.
        """
        if limit is not None:
            integer(limit, 'trade limit', 1)
        integer(start_ns, 'trade start')
        high = self._upper(ordinal)
        values = []
        for index in range(high - 1, -1, -1):
            row = self._trade(index)
            seq, stamp, match, price, quantity, printable, kind, broken = row
            if stamp < start_ns:
                break
            if broken <= ordinal or not quantity or not (printable or include_nonprintable):
                continue
            values.append(MarketTrade(seq, stamp, match, price, quantity, printable, kind.decode()))
            if limit is not None and len(values) >= limit:
                break
        values.reverse()
        return tuple(values)

    def bars(
        self,
        ordinal: int,
        cutoff: int,
        *,
        start_ns: int = 0,
        limit: Optional[int] = BAR_LIMIT,
        interval_ns: int = INTERVAL,
    ) -> Tuple[PriceVolumeBar, ...]:
        """Return the most recent one-minute bars as they stood at ``ordinal``, oldest first."""
        if interval_ns != INTERVAL:
            raise DataUnavailable('prepared profile supports one-minute bars')
        if limit is not None:
            integer(limit, 'bar limit', 1)
        values = []
        for index in range(self._meta['buckets'] - 1, -1, -1):
            bucket, offset, count = BUCKET.unpack_from(self._buckets, index * BUCKET.size)
            if bucket > cutoff:
                continue
            if bucket < start_ns:
                break
            low, high = 0, count
            while low < high:
                middle = (low + high) // 2
                row = BAR.unpack_from(self._bars, (offset + middle) * BAR.size)
                if row[0] <= ordinal:
                    low = middle + 1
                else:
                    high = middle
            if low:
                row = BAR.unpack_from(self._bars, (offset + low - 1) * BAR.size)
                if row[8]:
                    values.append(
                        PriceVolumeBar(
                            bucket, bucket + INTERVAL, cutoff, bucket + INTERVAL <= cutoff, *row[3:]
                        )
                    )
                    if limit is not None and len(values) >= limit:
                        break
        values.reverse()
        return tuple(values)

    def capture(self, ordinal: int, cutoff: int, *, include_bars: bool = True) -> Dict[str, Any]:
        """Return the bounded trade and bar tail an Agent may read.

        One row more than the limit is requested, to tell whether older data exists.
        """
        trades = self.trades(ordinal, cutoff, limit=TRADE_LIMIT + 1)
        bars = self.bars(ordinal, cutoff, limit=BAR_LIMIT + 1) if include_bars else ()
        return dict(
            trades=trades[-TRADE_LIMIT:],
            bars=bars[-BAR_LIMIT:],
            trades_complete=len(trades) <= TRADE_LIMIT,
            bars_complete=len(bars) <= BAR_LIMIT,
            trade_start_ns=(
                trades[-TRADE_LIMIT].timestamp_ns
                if len(trades) > TRADE_LIMIT
                else trades[0].timestamp_ns if trades else 0
            ),
        )
