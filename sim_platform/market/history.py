"""History algorithms over injected non-SQL scratch files; no resource creation."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import asdict
import json
import struct
from typing import Any, Dict, Iterator, List, Optional, Tuple

from .models import MarketError, MarketTrade, NewsRecord, PriceVolumeBar
from .window import BAR_LIMIT, INTERVAL, TRADE_LIMIT, HistoryCapture, HistoryWindow

# Fixed-size big-endian records in the scratch files.
# Trade: sequence, time, match ID, price, quantity, printable, kind, broken-at time.
_TRADE = struct.Struct('>QQQQQ?cQ')
# Match index entry: match ID, byte offset of its trade record.
_MATCH = struct.Struct('>QQ')
# Bar: start time, open, high, low, close, volume, trade count, notional.
_BAR = struct.Struct('>8Q')
_OFFSET = struct.Struct('>Q')
# Stored in a trade's broken-at field while the trade stands.
_NOT_BROKEN = 2**64 - 1
# Match IDs are indexed in this many files, by ID modulo the count.
_MATCH_BUCKETS = 256
# Bar files kept for the current time before the least recently used is removed.
_CACHED_INTERVALS = 8
_RECORDS_PER_READ = 1024


class MarketHistory:
    """Trades, bars and news observed so far, stored in scratch files.

    Playback appends each trade and publication as it occurs. Queries then read the
    files with a time cutoff, so a query never returns data later than the time it
    asks about. A broken trade is marked with the time it was broken, and stays
    visible to queries that ask about an earlier time.

    ``files`` supplies named append-only scratch files (size, read, write, append,
    remove, close). This class decides only what is stored in them.
    """

    def __init__(self, files: Any) -> None:
        self._files = files
        # Bar intervals with an up-to-date file, least recently used first.
        self._intervals: OrderedDict[int, None] = OrderedDict()
        # Highest match ID stored in each index bucket, to skip pointless scans.
        self._match_max: Dict[int, int] = {}
        self._closed = False
        # In-memory tail for Agent reads; created by prepare_agent_window().
        self._window: Optional[HistoryWindow] = None

    @staticmethod
    def _limit(limit: Optional[int]) -> None:
        if limit is not None and (type(limit) is not int or limit < 1):
            raise MarketError('history limit must be a positive integer')

    def _records(
        self, name: str, layout: struct.Struct, *, reverse: bool = False
    ) -> Iterator[Tuple[int, Tuple[Any, ...]]]:
        """Yield (byte offset, unpacked record) for every record in a scratch file."""
        size = self._files.size(name)
        if size % layout.size:
            raise MarketError('invalid cache record boundary')
        # Read blocks rather than opening a file per historical record.
        block = _RECORDS_PER_READ * layout.size
        if reverse:
            end = size
            while end:
                start = max(0, end - block)
                values = self._files.read(name, start, end - start)
                for offset in range(len(values) - layout.size, -1, -layout.size):
                    yield start + offset, layout.unpack_from(values, offset)
                end = start
        else:
            for start in range(0, size, block):
                values = self._files.read(name, start, min(block, size - start))
                for offset in range(0, len(values), layout.size):
                    yield start + offset, layout.unpack_from(values, offset)

    def _find_match(self, match: int) -> Optional[int]:
        """Return the byte offset of the trade with this match ID, or None."""
        bucket = match % _MATCH_BUCKETS
        if match > self._match_max.get(bucket, -1):
            return None
        for _, (identity, offset) in self._records('match-' + str(bucket), _MATCH, reverse=True):
            if identity == match:
                return offset
        return None

    def _append_bar(
        self, interval: int, stamp: int, price: int, quantity: int, name: Optional[str] = None
    ) -> None:
        """Add one trade to the bar that contains ``stamp``, starting a new bar if needed."""
        name = 'bar-' + str(interval) if name is None else name
        bucket = stamp // interval * interval
        size = self._files.size(name)
        if size:
            row = list(_BAR.unpack(self._files.read(name, size - _BAR.size, _BAR.size)))
            if row[0] == bucket:
                row[2], row[3], row[4] = max(row[2], price), min(row[3], price), price
                row[5] += quantity
                row[6] += 1
                row[7] += price * quantity
                self._files.write(name, size - _BAR.size, _BAR.pack(*row))
                if self._window is not None and interval == INTERVAL:
                    self._window.update_bar(row)
                return
        first = (bucket, price, price, price, price, quantity, 1, price * quantity)
        self._files.append(name, _BAR.pack(*first))
        if self._window is not None and interval == INTERVAL:
            self._window.update_bar(first)

    def add_trade(self, trade: MarketTrade) -> None:
        """Store a trade, index its match ID and update every cached bar interval."""
        if self._find_match(trade.match_id) is not None:
            raise MarketError('duplicate trade match number')
        row = (
            trade.sequence,
            trade.timestamp_ns,
            trade.match_id,
            trade.price,
            trade.quantity,
            trade.printable,
            trade.kind.encode(),
            _NOT_BROKEN,
        )
        offset = self._files.append('trades', _TRADE.pack(*row))
        if self._window is not None:
            self._window.add_trade(row)
        bucket = trade.match_id % _MATCH_BUCKETS
        self._files.append('match-' + str(bucket), _MATCH.pack(trade.match_id, offset))
        self._match_max[bucket] = max(trade.match_id, self._match_max.get(bucket, -1))
        if trade.printable and trade.quantity:
            for interval in self._intervals:
                self._append_bar(interval, trade.timestamp_ns, trade.price, trade.quantity)

    def _tail(self, name: str, layout: struct.Struct, limit: int) -> Tuple[Tuple[Any, ...], ...]:
        """Return the last ``limit`` records of a scratch file, oldest first."""
        rows = []
        for _, row in self._records(name, layout, reverse=True):
            rows.append(row)
            if len(rows) == limit:
                break
        return tuple(reversed(rows))

    def _build_bars(self, interval: int, start: int, cutoff: int, name: str) -> None:
        """Rebuild a bar file from the stored trades in [start, cutoff].

        Only printable trades that were not broken by ``cutoff`` count.
        """
        self._files.remove(name)
        current: Optional[List[int]] = None
        for _, row in self._records('trades', _TRADE):
            _, stamp, _, price, quantity, printable, _, broken = row
            if (
                start <= stamp <= cutoff
                and printable
                and quantity
                and (broken == _NOT_BROKEN or broken > cutoff)
            ):
                bucket = stamp // interval * interval
                if current is None or current[0] != bucket:
                    if current is not None:
                        self._files.append(name, _BAR.pack(*current))
                    current = [bucket, price, price, price, price, quantity, 1, price * quantity]
                else:
                    current[2], current[3], current[4] = (
                        max(current[2], price),
                        min(current[3], price),
                        price,
                    )
                    current[5] += quantity
                    current[6] += 1
                    current[7] += price * quantity
        if current is not None:
            self._files.append(name, _BAR.pack(*current))
        if self._window is not None and name == 'bar-' + str(INTERVAL):
            self._window.replace_bars(
                self._tail(name, _BAR, BAR_LIMIT), self._files.size(name) // _BAR.size
            )

    def break_trade(self, match: int, stamp: int) -> None:
        """Mark a trade as broken at ``stamp`` and rebuild the bars it contributed to."""
        offset = self._find_match(match)
        if offset is None:
            raise MarketError('unknown or duplicate broken trade')
        row = list(_TRADE.unpack(self._files.read('trades', offset, _TRADE.size)))
        if row[-1] != _NOT_BROKEN:
            raise MarketError('unknown or duplicate broken trade')
        row[-1] = stamp
        self._files.write('trades', offset, _TRADE.pack(*row))
        if self._window is not None:
            self._window.correct_trade(row)
        # Corrections are rare; rebuild active bar projections from the canonical records.
        for interval in self._intervals:
            self._build_bars(interval, 0, stamp, 'bar-' + str(interval))

    def add_news(self, news: NewsRecord) -> None:
        payload = json.dumps(asdict(news)).encode()
        offset = self._files.append('news', _OFFSET.pack(len(payload)) + payload)
        self._files.append('news-index', _OFFSET.pack(offset))

    def trades(
        self,
        cutoff: int,
        start: int = 0,
        include_nonprintable: bool = False,
        limit: Optional[int] = None,
    ) -> Tuple[MarketTrade, ...]:
        """Return trades in [start, cutoff] that still stood at ``cutoff``, oldest first.

        ``limit`` keeps only the most recent trades.
        """
        self._limit(limit)
        values = []
        for _, row in self._records('trades', _TRADE, reverse=limit is not None):
            seq, stamp, match, price, quantity, printable, kind, broken = row
            if start <= stamp <= cutoff and quantity and (broken == _NOT_BROKEN or broken > cutoff):
                if printable or include_nonprintable:
                    values.append(
                        MarketTrade(seq, stamp, match, price, quantity, printable, kind.decode())
                    )
                    if limit is not None and len(values) == limit:
                        break
        if limit is not None:
            values.reverse()
        return tuple(values)

    def news(self, cutoff: int, limit: Optional[int] = None) -> Tuple[NewsRecord, ...]:
        """Return publications available by ``cutoff``, oldest first."""
        self._limit(limit)
        values = []
        for _, (offset,) in self._records('news-index', _OFFSET, reverse=limit is not None):
            size = _OFFSET.unpack(self._files.read('news', offset, _OFFSET.size))[0]
            item = NewsRecord(**json.loads(self._files.read('news', offset + _OFFSET.size, size)))
            if item.available_time_ns <= cutoff:
                values.append(item)
                if limit is not None and len(values) == limit:
                    break
        if limit is not None:
            values.reverse()
        return tuple(values)

    def bars(
        self,
        interval: int,
        cutoff: int,
        current_time: int,
        start: int = 0,
        limit: Optional[int] = None,
    ) -> Tuple[PriceVolumeBar, ...]:
        """Return bars of ``interval`` nanoseconds for trades in [start, cutoff].

        Bars up to the current time from the start of the day are kept in a file that
        each new trade updates. Any other range is rebuilt from the stored trades.
        """
        self._limit(limit)
        cached = cutoff == current_time and start == 0
        name = 'bar-' + str(interval) if cached else 'historical-bars'
        if cached and interval in self._intervals:
            self._intervals.move_to_end(interval)
        else:
            self._build_bars(interval, start, cutoff, name)
            if cached:
                self._intervals[interval] = None
                if len(self._intervals) > _CACHED_INTERVALS:
                    evicted = next(
                        value
                        for value in self._intervals
                        if self._window is None or value != INTERVAL
                    )
                    self._intervals.pop(evicted)
                    self._files.remove('bar-' + str(evicted))
        values = []
        for _, (stamp, *rest) in self._records(name, _BAR, reverse=limit is not None):
            values.append(
                PriceVolumeBar(stamp, stamp + interval, cutoff, stamp + interval <= cutoff, *rest)
            )
            if limit is not None and len(values) == limit:
                break
        if limit is not None:
            values.reverse()
        return tuple(values)

    def prepare_agent_window(self, cutoff: int) -> None:
        """Prime the existing minute aggregate and bounded memory projection at setup."""
        interval = INTERVAL
        self.bars(interval, cutoff, cutoff, limit=BAR_LIMIT)
        name = 'bar-' + str(interval)
        self._window = HistoryWindow(
            self._tail('trades', _TRADE, TRADE_LIMIT),
            self._tail(name, _BAR, BAR_LIMIT),
            self._files.size('trades') // _TRADE.size,
            self._files.size(name) // _BAR.size,
        )

    def capture_agent_window(self, cutoff: int, *, include_bars: bool = True) -> HistoryCapture:
        """Owner-only memory read; retention includes broken/nonprintable raw rows."""
        if self._window is None:
            raise MarketError('agent history has not been prepared')
        return self._window.capture(cutoff, include_bars)

    def agent_window(self, cutoff: int, *, include_bars: bool = True) -> Dict[str, Any]:
        return self.capture_agent_window(cutoff, include_bars=include_bars).materialize()

    def close(self) -> None:
        if not self._closed:
            self._files.close()
            self._closed = True
