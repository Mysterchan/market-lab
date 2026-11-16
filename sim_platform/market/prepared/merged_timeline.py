"""One timeline index over several symbols' prepared recordings, for timed experiments.

Live and Agent experiments schedule against a timeline index (``prefix``, ``event_time``,
``market_sequence``) and step the market itself. ``MergedTimelineReader`` gives a
MergedMarket that index. Its order is the MergedMarket's stepping order: by market time,
then universe order, then each recording's own order, so index ``k`` is always the
``k``-th event the merged market emits. Market sequences add up the symbols' own
sequences, as ``MergedMarket.sequence`` does.
"""

from __future__ import annotations

from array import array
from bisect import bisect_left
import heapq
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

from sim_platform.domain.models import ContractError, integer
from sim_platform.market.models import MarketSnapshot, MarketView


class MergedHistory:
    """History queries addressed by merged prefix, answered by one symbol's history.

    Views of a merged run show the first symbol's trades and bars; per-symbol reads go
    through ``MergedTimelineReader.symbol_reader``.
    """

    def __init__(self, timeline: 'MergedTimelineReader', order: int = 0) -> None:
        self._timeline, self._order = timeline, order

    def _call(self, name: str, prefix: int, cutoff: int, **options: Any) -> Any:
        reader = self._timeline._readers[self._order]
        local = self._timeline._local(self._order, prefix)
        return getattr(reader.history, name)(local, cutoff, **options)

    def trades(self, prefix: int, cutoff: int, **options: Any) -> Any:
        return self._call('trades', prefix, cutoff, **options)

    def bars(self, prefix: int, cutoff: int, **options: Any) -> Any:
        return self._call('bars', prefix, cutoff, **options)

    def capture(self, prefix: int, cutoff: int, **options: Any) -> Any:
        return self._call('capture', prefix, cutoff, **options)


class MergedTimelineReader:
    """Read-only merged index; borrows the symbols' readers (the sessions own them).

    Args:
        universe: Symbols in universe order.
        readers: Each symbol's prepared reader, in the same order.
    """

    def __init__(self, universe: Sequence[str], readers: Sequence[Any]) -> None:
        if not universe or len(universe) != len(readers):
            raise ContractError('a merged timeline needs one reader per symbol')
        self.universe: Tuple[str, ...] = tuple(universe)
        self._readers = list(readers)
        frames = [r.manifest['frames'] for r in readers]

        def events(order: int) -> Iterator[Tuple[int, int, int]]:
            reader = self._readers[order]
            for local in range(frames[order]):
                yield reader.event_time(local), order, local

        self._times = array('q')
        self._symbols = array('b')
        # Merged index of each symbol's events, in that symbol's order.
        self._positions: List[array] = [array('q') for _ in readers]
        for index, (stamp, order, _) in enumerate(
            heapq.merge(*(events(order) for order in range(len(readers))))
        ):
            self._times.append(stamp)
            self._symbols.append(order)
            self._positions[order].append(index)
        manifests = [r.manifest for r in readers]
        calendars = [m.get('calendar') for m in manifests]
        self.manifest: Dict[str, Any] = dict(
            manifests[0],
            frames=len(self._times),
            first_ns=min(m['first_ns'] for m in manifests),
            last_ns=max(m['last_ns'] for m in manifests),
            universe=list(self.universe),
        )
        # Skip closed-market time only when every symbol shares the same calendar.
        if calendars[0] is not None and all(c == calendars[0] for c in calendars):
            self.manifest['calendar'] = calendars[0]
        else:
            self.manifest.pop('calendar', None)
        self.directory = readers[0].directory
        self.directories = [r.directory for r in readers]
        # The experiment's announcements (NewsRecord), oldest first; set by MergedMarket.
        self.news: Tuple[Any, ...] = ()
        self.history = MergedHistory(self)

    # --------------------------------------------------------------- indexing

    def symbol_reader(self, symbol: str) -> Any:
        """The prepared reader of one symbol of the universe."""
        if symbol not in self.universe:
            raise ContractError('symbol is outside the experiment universe')
        return self._readers[self.universe.index(symbol)]

    def _local(self, order: int, prefix: int) -> int:
        """How many of a symbol's events lie within the first ``prefix`` merged events."""
        return bisect_left(self._positions[order], prefix)

    def prefix(self, stamp: int) -> int:
        """Number of merged events at or before ``stamp``."""
        return sum(reader.prefix(stamp) for reader in self._readers)

    def event_time(self, index: int) -> int:
        integer(index, 'event index')
        if index >= len(self._times):
            raise ContractError('event outside the merged timeline')
        return self._times[index]

    def source_sequence(self, prefix: int) -> int:
        return sum(
            reader.source_sequence(self._local(order, prefix))
            for order, reader in enumerate(self._readers)
        )

    def market_sequence(self, prefix: int) -> int:
        integer(prefix, 'merged prefix')
        if prefix > len(self._times):
            raise ContractError('prefix outside the merged timeline')
        return sum(
            reader.market_sequence(self._local(order, prefix))
            for order, reader in enumerate(self._readers)
        )

    def symbol_at(self, index: int) -> str:
        """The symbol of the merged event at ``index``."""
        return self.universe[self._symbols[index]]

    # ---------------------------------------------------------------- queries

    def snapshot_at(self, stamp: int) -> MarketSnapshot:
        """Every symbol's book at ``stamp``, as a MarketView led by the latest event's symbol."""
        prefix = self.prefix(stamp)
        books = {
            symbol: reader.snapshot_at(stamp)
            for symbol, reader in zip(self.universe, self._readers)
        }
        lead = self.symbol_at(prefix - 1) if prefix else self.universe[0]
        sequence = self.market_sequence(prefix)
        time_ns = max(book.time_ns for book in books.values())
        from dataclasses import replace

        stamped = {
            symbol: replace(book, sequence=sequence, time_ns=time_ns)
            for symbol, book in books.items()
        }
        return MarketView.of(stamped[lead], stamped)

    def news_at(self, stamp: int, limit: Optional[int] = None) -> Tuple[Any, ...]:
        """The experiment's publications available at or before ``stamp``."""
        visible = tuple(n for n in self.news if n.available_time_ns <= stamp)
        return visible[-limit:] if limit else visible

    def close(self) -> None:
        """The sessions own and close their readers."""
