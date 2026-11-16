"""Several prepared symbols played as one ordered market (docs/architecture/multi-symbol-design.md).

MergedMarket owns one PreparedSession per symbol and delivers their events as a single
timeline: by market time, then in universe order (the order the symbols were chosen),
then in each source's own order. Its merged sequence is the sum of the sessions'
sequences, so it rises by exactly one per market event, as a single session's does.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from sim_platform.domain.models import ContractError, integer
from sim_platform.market.models import (
    DataUnavailable,
    MarketError,
    MarketEvent,
    MarketSnapshot,
    MarketTrade,
    MarketView,
    NewsRecord,
    PriceVolumeBar,
)
from sim_platform.market.pacing import PlaybackSchedule
from sim_platform.market.window import INTERVAL

from .session import PreparedSession

Subscriber = Callable[[MarketEvent], None]


class MergedMarket:
    """One experiment's market over several symbols; owns its per-symbol sessions.

    The experiment's execution owner claims this object, never the sessions. Sessions
    are created by the caller, loaded through load() and closed by close().

    Args:
        sessions: One unloaded PreparedSession per symbol, in universe order.
    """

    def __init__(self, sessions: Sequence[PreparedSession]) -> None:
        if not sessions:
            raise ContractError('a merged market needs at least one symbol')
        self._sessions: Dict[str, PreparedSession] = {}
        for session in sessions:
            symbol = session.snapshot.symbol
            if symbol in self._sessions:
                raise ContractError('each symbol may appear once in the universe')
            self._sessions[symbol] = session
        self.universe: Tuple[str, ...] = tuple(self._sessions)
        self._paths: List[Path] = []
        self._implicit_eof = False
        self._status = 'unloaded'
        self._current = self.universe[0]
        self._owner: Optional[object] = None
        self._callbacks: List[Subscriber] = []
        self._closed = False
        # The experiment's announcements, oldest first; visible from available_time_ns.
        self._news: Tuple[NewsRecord, ...] = ()
        self._on_close: Callable[[], None] = lambda: None
        self._origin_ns = 0
        self._playback = PlaybackSchedule(self)
        self.is_notifying = False

    @classmethod
    def from_sessions(cls, sessions: Sequence[PreparedSession], start_ns: int) -> MergedMarket:
        """Merge sessions already loaded and positioned at ``start_ns`` (operation Replay)."""
        market = cls(sessions)
        market._status = 'paused'
        market._origin_ns = start_ns
        return market

    # ------------------------------------------------------------------ state

    def symbol_market(self, symbol: str) -> PreparedSession:
        """The prepared session of one symbol of the universe."""
        if symbol not in self._sessions:
            raise ContractError('symbol is outside the experiment universe')
        return self._sessions[symbol]

    @property
    def sequence(self) -> int:
        return sum(s.snapshot.sequence for s in self._sessions.values())

    @property
    def time_ns(self) -> int:
        return max(s.time_ns for s in self._sessions.values())

    @property
    def status(self) -> str:
        return self._status

    def _book(self, symbol: str) -> MarketSnapshot:
        """A symbol's book stamped with the merged time, sequence and status."""
        return replace(
            self._sessions[symbol].snapshot,
            time_ns=self.time_ns,
            sequence=self.sequence,
            status=self._status,
        )

    @property
    def snapshot(self) -> MarketView:
        """The book of the symbol of the latest event, carrying every symbol's book."""
        books = {symbol: self._book(symbol) for symbol in self.universe}
        return MarketView.of(books[self._current], books)

    @property
    def origin_ns(self) -> int:
        return self._origin_ns

    @property
    def merged_prefix(self) -> int:
        return sum(s.merged_prefix for s in self._sessions.values())

    @property
    def has_execution_owner(self) -> bool:
        return self._owner is not None

    @property
    def calendar(self) -> Optional[Dict[str, Any]]:
        """The shared calendar of price-bar recordings, without closed-market gaps."""
        calendar = self._sessions[self.universe[0]].calendar
        if calendar is None:
            return None
        return {k: v for k, v in calendar.items() if k != 'gaps'}

    def _check(self) -> None:
        if self._closed or self.is_notifying:
            raise MarketError('merged market is closed or publishing callbacks')

    def on_close(self, callback: Callable[[], None]) -> None:
        self._on_close = callback

    def claim_execution(self, owner: object) -> None:
        self._check()
        if self._owner is not None:
            raise ContractError('merged market already has an execution owner')
        self._owner = owner

    def release_execution(self, owner: object) -> None:
        if self._owner is owner:
            self._owner = None

    # ------------------------------------------------------------ preparation

    def load(
        self,
        paths: Sequence[Any],
        *,
        news: Sequence[NewsRecord] = (),
        allow_implicit_eof: bool = False,
    ) -> MarketSnapshot:
        """Choose each symbol's source; ``paths`` follows the universe order.

        A workspace prepares one source at a time, so prepare() loads them in turn.
        """
        self._check()
        self._news = tuple(sorted(news, key=lambda record: record.available_time_ns))
        if len(paths) != len(self.universe):
            raise ContractError('give one source per symbol')
        self._paths = [Path(path) for path in paths]
        self._implicit_eof = allow_implicit_eof
        self._status = 'loading'
        self.prepare()
        return self.snapshot

    def prepare(self) -> bool:
        """Advance the symbols' preparations in turn; True once all are ready."""
        self._check()
        for symbol, path in zip(self.universe, self._paths):
            session = self._sessions[symbol]
            if session.status == 'unloaded':
                session.load(path, allow_implicit_eof=self._implicit_eof)
            if not session.prepare():
                return False
        starts = {
            (s.calendar or {}).get('start_date') for s in self._sessions.values() if s.calendar
        }
        if len(starts) > 1:
            raise ContractError('price-bar recordings must share a first date; prepare them so')
        if self._status == 'loading':
            self._status = 'ready'
        return True

    def preparation_progress(self) -> Dict[str, Any]:
        return {
            symbol: session.preparation_progress() for symbol, session in self._sessions.items()
        }

    def seek(self, time_ns: int) -> MarketSnapshot:
        """Position every symbol at ``time_ns``, or at its last event if it ends sooner."""
        self._check()
        integer(time_ns, 'start time')
        if self._owner is not None or self._status not in ('ready', 'paused'):
            raise ContractError('seek requires an unbound ready market')
        last = max(s.reader.manifest['last_ns'] for s in self._sessions.values())
        if time_ns > last:
            raise DataUnavailable('start exceeds prepared source coverage')
        for session in self._sessions.values():
            session.seek(min(time_ns, session.reader.manifest['last_ns']))
        self._origin_ns, self._status = time_ns, 'paused'
        return self.snapshot

    def start_position(self, mode: str, time_ns: Optional[int] = None) -> MarketSnapshot:
        """Like PreparedSession.start_position; ``first_book`` waits for every symbol."""
        from .session import REGULAR_OPEN_NS

        if mode == 'beginning':
            return self.seek(0)
        if mode == 'first_book':
            firsts = [
                s.reader.manifest['history']['first_book_ns'] for s in self._sessions.values()
            ]
            if any(t is None for t in firsts):
                raise DataUnavailable('a symbol never shows a two-sided book')
            return self.seek(max(firsts))
        if mode == 'regular':
            return self.seek(REGULAR_OPEN_NS)
        if mode != 'custom' or time_ns is None:
            raise ContractError('a custom start needs a time')
        return self.seek(time_ns)

    def source_descriptor(self) -> Optional[Dict[str, Any]]:
        """Each symbol's source, for Replay to prepare and merge them again."""
        sources = [self._sessions[symbol].source_descriptor() for symbol in self.universe]
        if any(source is None for source in sources):
            return None
        return dict(kind='prepared-merged', universe=list(self.universe), sources=sources)

    def prepared_descriptor(self) -> Dict[str, Any]:
        """The prepared artifact of each symbol, saved with the run for views."""
        descriptor: Dict[str, Any] = dict(
            merged=True,
            universe=list(self.universe),
            symbols={
                symbol: self._sessions[symbol].prepared_descriptor() for symbol in self.universe
            },
        )
        if self.calendar is not None:
            descriptor['calendar'] = self.calendar
        return descriptor

    # --------------------------------------------------------------- playback

    def _next(self) -> Optional[str]:
        """The symbol whose next event comes first; universe order breaks ties."""
        best: Optional[Tuple[int, int]] = None
        chosen = None
        for order, symbol in enumerate(self.universe):
            next_ns = self._sessions[symbol].next_event_time_ns
            if next_ns is not None and (best is None or (next_ns, order) < best):
                best, chosen = (next_ns, order), symbol
        return chosen

    @property
    def next_event_time_ns(self) -> Optional[int]:
        symbol = self._next()
        return None if symbol is None else self._sessions[symbol].next_event_time_ns

    def _emit(self, event: MarketEvent) -> MarketEvent:
        if not self._callbacks:
            return event
        self.is_notifying = True
        try:
            for callback in tuple(self._callbacks):
                callback(event)
        finally:
            self.is_notifying = False
        return event

    def subscribe(self, callback: Subscriber) -> Callable[[], None]:
        self._check()
        self._callbacks.append(callback)

        def unsubscribe() -> None:
            if callback in self._callbacks:
                self._callbacks.remove(callback)

        return unsubscribe

    def status_event(self) -> MarketEvent:
        return MarketEvent('status', self.time_ns, self.sequence, '', self.snapshot)

    def _advance(self) -> MarketEvent:
        symbol = self._next()
        if symbol is None:
            self._status = 'completed'
            return self._emit(self.status_event())
        event = self._sessions[symbol].step()
        self._current = symbol
        snapshot = self.snapshot
        return self._emit(
            replace(event, sequence=snapshot.sequence, time_ns=snapshot.time_ns, snapshot=snapshot)
        )

    def play(self, speed: Optional[float] = None) -> MarketEvent:
        self._check()
        if self._status not in ('ready', 'paused'):
            raise MarketError('play requires a ready market')
        self._playback.play(speed)
        self._status = 'playing'
        return self._emit(self.status_event())

    def pause(self) -> MarketEvent:
        self._check()
        if self._status not in ('ready', 'paused', 'playing'):
            raise MarketError('pause requires an active market')
        self._playback.pause()
        self._status = 'paused'
        return self._emit(self.status_event())

    def step(self) -> MarketEvent:
        self._check()
        if self._status not in ('ready', 'paused'):
            raise MarketError('step requires a paused market')
        self._status = 'paused'
        return self._advance()

    def pump(self, count: int = 1) -> Tuple[MarketEvent, ...]:
        self._check()
        integer(count, 'pump count', 1)
        values = []
        for _ in range(count):
            if self._status != 'playing':
                break
            values.append(self._advance())
        return tuple(values)

    def poll(self, *, max_events: int = 1, budget_seconds: Optional[float] = None) -> None:
        self._playback.poll(self.pump, max_events=max_events, budget_seconds=budget_seconds)

    def idle_candidate(self, end_prefix: int) -> Optional[MarketEvent]:
        """Stable-span batching is per source; a merged timeline steps every event."""
        return None

    def apply_idle(self, end_prefix: int, event: MarketEvent) -> None:
        raise ContractError('a merged market has no idle spans')

    def fail(self, reason: str) -> MarketEvent:
        self._status = 'failed'
        return self._emit(replace(self.status_event(), kind='integrity', message=reason))

    # ---------------------------------------------------------------- queries

    # ------------------------------------------------------ timed experiments

    @property
    def reader(self) -> Any:
        """The merged timeline index Live and Agent experiments schedule against.

        Built once all symbols are prepared; its order is this market's stepping order.
        """
        cached = self.__dict__.get('_reader')
        if cached is None:
            from .merged_timeline import MergedTimelineReader

            cached = MergedTimelineReader(
                self.universe, [self._sessions[s].reader for s in self.universe]
            )
            cached.news = self._news
            self.__dict__['_reader'] = cached
        return cached

    def prepare_agent_history(self) -> None:
        for session in self._sessions.values():
            session.prepare_agent_history()
        self.reader  # noqa: B018 - builds the index before the run starts

    def capture_agent_history(
        self, *, include_bars: bool = True, symbol: Optional[str] = None
    ) -> Dict[str, Any]:
        """One symbol's trades and bars so far (the first symbol unless named)."""
        return self.symbol_market(symbol or self.universe[0]).capture_agent_history(
            include_bars=include_bars
        )

    def agent_history_window(self, *, include_bars: bool = True) -> Dict[str, Any]:
        return self.capture_agent_history(include_bars=include_bars)

    def query_cutoff(self, as_of_ns: Optional[int] = None) -> int:
        target = self.time_ns if as_of_ns is None else as_of_ns
        integer(target, 'query time')
        return target

    def book(self, symbol: Optional[str] = None) -> MarketSnapshot:
        return self._book(symbol or self._current)

    def trades(self, *, limit: Optional[int] = None, **options: Any) -> Tuple[MarketTrade, ...]:
        """Trades of the latest event's symbol; use symbol_market(symbol) for another."""
        return self._sessions[self._current].trades(limit=limit, **options)

    def bars(
        self, interval_ns: int = INTERVAL, *, limit: Optional[int] = None, **options: Any
    ) -> Tuple[PriceVolumeBar, ...]:
        """Bars of the latest event's symbol; use symbol_market(symbol) for another."""
        return self._sessions[self._current].bars(interval_ns, limit=limit, **options)

    def news(
        self, *, as_of_ns: Optional[int] = None, limit: Optional[int] = None, **options: Any
    ) -> Tuple[NewsRecord, ...]:
        """Announcements published by the merged clock (or ``as_of_ns``, not later).

        One news file serves every symbol of the experiment; each record is visible from
        its ``available_time_ns``, the same point-in-time rule as a single symbol.
        """
        cutoff = min(self.query_cutoff(as_of_ns), self.time_ns)
        visible = tuple(n for n in self._news if n.available_time_ns <= cutoff)
        return visible[-limit:] if limit else visible

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._status = 'closed'
        try:
            for session in self._sessions.values():
                session.close()
        finally:
            self._on_close()

    def __enter__(self) -> MergedMarket:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
