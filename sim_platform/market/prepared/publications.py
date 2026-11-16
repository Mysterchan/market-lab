"""Immutable publication overlay: news precedes ITCH at equal timestamps.

The underlying prepared ordinal still counts source events only; news changes
market time but does not increment that financial sequence. Merged cursor
indexes are separate and include both. No copy or rebuild of the source book.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import replace
from typing import Any, Iterable, Optional, Sequence, Tuple

from sim_platform.domain.models import ContractError, integer
from sim_platform.market.models import MarketEvent, MarketSnapshot, NewsRecord, time_ns

# Two ways of counting events appear below.
#   source prefix: how many source (ITCH) events have occurred.
#   merged prefix: how many events of the merged timeline, source and news together.


class PublicationHistory:
    """History queries addressed by merged prefix, answered by the source history."""

    def __init__(self, base: Any, positions: Sequence[int]) -> None:
        self._base, self._positions = base, positions

    def _prefix(self, prefix: int) -> int:
        """Convert a merged prefix to a source prefix by removing the news before it."""
        return prefix - bisect_left(self._positions, prefix)

    def trades(self, prefix: int, cutoff: int, **options: Any) -> Any:
        return self._base.trades(self._prefix(prefix), cutoff, **options)

    def bars(self, prefix: int, cutoff: int, **options: Any) -> Any:
        return self._base.bars(self._prefix(prefix), cutoff, **options)

    def capture(self, prefix: int, cutoff: int, **options: Any) -> Any:
        return self._base.capture(self._prefix(prefix), cutoff, **options)


class PublicationReader:
    """A prepared market with publications merged into its timeline.

    Each publication takes one position in the merged timeline, placed before any
    source event with the same time. Owns the borrowed reader lease after successful
    construction; ``close()`` closes it.
    """

    def __init__(self, reader: Any, records: Iterable[NewsRecord]) -> None:
        records = tuple(records)
        ids = set()
        for item in records:
            if not isinstance(item, NewsRecord):
                raise ContractError('news must contain NewsRecord values')
            time_ns(item.available_time_ns)
            if (
                type(item.revision) is not int
                or item.revision < 1
                or any(
                    not isinstance(v, str) or not v
                    for v in (item.record_id, item.period, item.headline, item.provenance)
                )
                or (item.record_id, item.revision) in ids
            ):
                raise ContractError('invalid publication fields or duplicate revision')
            ids.add((item.record_id, item.revision))
        self._base = reader
        self.news = tuple(
            sorted(records, key=lambda n: (n.available_time_ns, n.record_id, n.revision))
        )
        self._times = tuple(n.available_time_ns for n in self.news)
        # Merged-timeline index of each publication: the source events strictly before
        # its time, plus the publications sorted ahead of it.
        self._positions = tuple(
            reader.prefix(n.available_time_ns - 1) + i if n.available_time_ns else i
            for i, n in enumerate(self.news)
        )
        self.manifest = dict(
            reader.manifest,
            frames=reader.manifest['frames'] + len(self.news),
            first_ns=(
                min(reader.manifest['first_ns'], self._times[0])
                if records
                else reader.manifest['first_ns']
            ),
            last_ns=(
                max(reader.manifest['last_ns'], self._times[-1])
                if records
                else reader.manifest['last_ns']
            ),
        )
        self.directory = reader.directory
        self.history = PublicationHistory(reader.history, self._positions)

    def prefix(self, stamp: int) -> int:
        """Number of merged events with a time at or before ``stamp``."""
        return self._base.prefix(stamp) + bisect_right(self._times, stamp)

    def _source_prefix(self, prefix: int) -> int:
        return prefix - bisect_left(self._positions, prefix)

    def event(self, index: int) -> MarketEvent:
        """Return the merged event at ``index``: a publication or a source event."""
        integer(index, 'event index')
        if index >= self.manifest['frames']:
            raise ContractError('event outside prepared publication range')
        news_index = bisect_left(self._positions, index)
        if news_index < len(self._positions) and self._positions[news_index] == index:
            item = self.news[news_index]
            source_prefix = index - news_index
            snapshot = self._base.snapshot_prefix(source_prefix)
            snapshot = replace(snapshot, time_ns=item.available_time_ns, status='playing')
            return MarketEvent('news', snapshot.time_ns, snapshot.sequence, '', snapshot, news=item)
        return self._base.event(index - news_index)

    def event_time(self, index: int) -> int:
        integer(index, 'event index')
        if index >= self.manifest['frames']:
            raise ContractError('event outside prepared publication range')
        news_index = bisect_left(self._positions, index)
        if news_index < len(self._positions) and self._positions[news_index] == index:
            return self._times[news_index]
        return self._base.event_time(index - news_index)

    def snapshot_at(self, stamp: int) -> MarketSnapshot:
        prefix = self.prefix(stamp)
        return self.event(prefix - 1).snapshot if prefix else self._base.snapshot_prefix(0)

    def source_sequence(self, prefix: int) -> int:
        return self._base.source_sequence(self._source_prefix(prefix))

    def market_sequence(self, prefix: int) -> int:
        integer(prefix, 'publication prefix')
        if prefix > self.manifest['frames']:
            raise ContractError('prefix outside publication range')
        return self._base.market_sequence(self._source_prefix(prefix))

    def news_for_prefix(self, prefix: int, limit: Optional[int] = None) -> Tuple[NewsRecord, ...]:
        """Publications delivered within the first ``prefix`` merged events."""
        count = bisect_left(self._positions, prefix)
        values = self.news[:count]
        return values[-limit:] if limit is not None else values

    def news_at(self, stamp: int, limit: Optional[int] = None) -> Tuple[NewsRecord, ...]:
        """Publications available at or before ``stamp``."""
        end = bisect_right(self._times, stamp)
        values = self.news[:end]
        return values[-limit:] if limit is not None else values

    def close(self) -> None:
        self._base.close()
