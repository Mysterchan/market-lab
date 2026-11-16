"""App-created playback with explicit non-SQL history ownership."""

from __future__ import annotations
import logging

from collections import deque
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

from sim_platform.resources import cleanup

from .history import MarketHistory
from .models import (
    DAY_NS,
    DataUnavailable,
    FutureDataError,
    MarketError,
    MarketEvent,
    MarketSnapshot,
    MarketTrade,
    NewsRecord,
    PriceVolumeBar,
    time_ns,
)
from .pacing import PlaybackSchedule
from .window import INTERVAL, HistoryCapture

_log = logging.getLogger(__name__)

Subscriber = Callable[[MarketEvent], Any]


class MarketPlayback:
    """A market the caller plays, pauses and steps, with queries over what has occurred.

    This joins the native market state (``engine``) with the recorded history: every
    event the engine produces is stored and then delivered to subscribers. Queries
    accept an ``as_of_ns`` time and never return anything later than the market's
    current time.

    Subscribers are called synchronously. They may read, but must not control the
    session; an exception in one subscriber is recorded and does not stop the others.
    """

    def __init__(self, engine: Any, history: MarketHistory) -> None:
        self._engine, self._history = engine, history
        self._callbacks: List[Subscriber] = []
        self._errors: Deque[str] = deque(maxlen=100)
        self._notifying = self._closed = False
        self._on_close: Callable[[], None] = lambda: None
        self._playback = PlaybackSchedule(self)

    def on_close(self, callback: Callable[[], None]) -> None:
        self._on_close = callback

    @property
    def snapshot(self) -> MarketSnapshot:
        return self._engine.snapshot

    @property
    def status(self) -> str:
        return self._engine.status

    @property
    def time_ns(self) -> int:
        return self._engine.time_ns

    @property
    def is_notifying(self) -> bool:
        """True while subscribers are being called; controls are refused until they return."""
        return self._notifying

    @property
    def has_execution_owner(self) -> bool:
        return self._engine.has_execution_owner

    @property
    def subscriber_errors(self) -> Tuple[str, ...]:
        """Messages of the most recent exceptions raised by subscribers."""
        return tuple(self._errors)

    def claim_execution(self, owner: object) -> None:
        """Let one execution session drive this market; a second claim is refused."""
        self._control()
        self._engine.claim_execution(owner)

    def release_execution(self, owner: object) -> None:
        self._engine.release_execution(owner)

    def _control(self) -> None:
        """Refuse a control call on a closed session or from inside a subscriber."""
        if self._closed:
            raise MarketError('playback is closed')
        if self._notifying:
            raise MarketError('session controls are forbidden inside subscribers')

    def subscribe(self, callback: Subscriber) -> Callable[[], None]:
        """Call ``callback`` with every event; the returned function unsubscribes it."""
        self._control()
        if not callable(callback):
            raise MarketError('subscriber must be callable')
        self._callbacks.append(callback)

        def unsubscribe() -> None:
            if callback in self._callbacks:
                self._callbacks.remove(callback)

        return unsubscribe

    def _notify(self, event: MarketEvent) -> MarketEvent:
        self._notifying = True
        try:
            for callback in tuple(self._callbacks):
                try:
                    callback(event)
                except Exception as exc:
                    _log.warning('Handled failure in _notify', exc_info=True)
                    self._errors.append(str(exc))
        finally:
            self._notifying = False
        return event

    def _record(self, event: MarketEvent) -> MarketEvent:
        """Store what the event carries, then deliver it; a storage error fails the market."""
        try:
            if event.trade:
                self._history.add_trade(event.trade)
            if event.news:
                self._history.add_news(event.news)
            if event.broken_match_id is not None:
                self._history.break_trade(event.broken_match_id, event.time_ns)
        except Exception as exc:
            _log.warning('Handled failure in _record', exc_info=True)
            return self._notify(self._engine.fail(str(exc)))
        return self._notify(event)

    @property
    def next_event_time_ns(self) -> Optional[int]:
        return self._engine.next_event_time_ns

    def source_descriptor(self) -> Any:
        return self._engine.source_descriptor()

    def load(self, path: Any, **options: Any) -> Any:
        self._control()
        return self._engine.load(path, **options)

    def load_stream(self, feed: Any, **options: Any) -> Any:
        self._control()
        return self._engine.load_stream(feed, **options)

    def play(self, speed: Optional[float] = None) -> Any:
        """Start playback. ``speed`` paces it against real time; None plays unpaced."""
        self._control()
        self._playback.play(speed)
        result = self._engine.play()
        self._notify(self._engine.status_event())
        return result

    def pause(self) -> Any:
        self._control()
        self._playback.pause()
        result = self._engine.pause()
        self._notify(self._engine.status_event())
        return result

    def step(self) -> MarketEvent:
        """Advance by exactly one event."""
        self._control()
        return self._record(self._engine.step())

    def pump(self, count: int = 1) -> Tuple[MarketEvent, ...]:
        """Advance up to ``count`` events while playing; stops when playback stops."""
        self._control()
        if type(count) is not int or count < 1:
            raise MarketError('pump count must be a positive integer')
        events = []
        for _ in range(count):
            if self.status != 'playing':
                break
            events.append(self._record(self._engine.pump(1)[0]))
        return tuple(events)

    def poll(self, *, max_events: int = 1, budget_seconds: Optional[float] = None) -> None:
        """Advance the events that are due at the current playback speed."""
        self._control()
        self._playback.poll(self.pump, max_events=max_events, budget_seconds=budget_seconds)

    def _cutoff(self, as_of_ns: Optional[int]) -> int:
        """Resolve a query time; None means now, and a future time is refused."""
        if self._closed:
            raise DataUnavailable('playback is closed')
        return self._engine.query_cutoff(as_of_ns)

    def book(self, *, as_of_ns: Optional[int] = None) -> Any:
        self._cutoff(as_of_ns)
        return self._engine.book(as_of_ns=as_of_ns)

    def close(self) -> None:
        if self._closed:
            return
        self._control()
        try:
            cleanup(self._engine.close, self._history.close)
        finally:
            self._closed = True
            self._on_close()

    def __enter__(self) -> MarketPlayback:
        self._control()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def trades(
        self,
        *,
        as_of_ns: Optional[int] = None,
        start_ns: int = 0,
        include_nonprintable: bool = False,
        limit: Optional[int] = None,
    ) -> Tuple[MarketTrade, ...]:
        cutoff = self._cutoff(as_of_ns)
        time_ns(start_ns)
        if start_ns > cutoff:
            raise FutureDataError('query starts after cutoff')
        return self._history.trades(cutoff, start_ns, include_nonprintable, limit)

    def news(
        self, *, as_of_ns: Optional[int] = None, limit: Optional[int] = None
    ) -> Tuple[NewsRecord, ...]:
        return self._history.news(self._cutoff(as_of_ns), limit)

    def bars(
        self,
        interval_ns: int = INTERVAL,
        *,
        as_of_ns: Optional[int] = None,
        start_ns: int = 0,
        limit: Optional[int] = None,
    ) -> Tuple[PriceVolumeBar, ...]:
        cutoff = self._cutoff(as_of_ns)
        time_ns(start_ns)
        if start_ns > cutoff:
            raise FutureDataError('query starts after cutoff')
        if type(interval_ns) is not int or interval_ns < 1 or interval_ns > DAY_NS:
            raise MarketError('bar interval must be a positive integer no longer than one day')
        return self._history.bars(interval_ns, cutoff, self.time_ns, start_ns, limit)

    def prepare_agent_history(self) -> None:
        """Initialize the existing minute projection before starting an owner."""
        self._history.prepare_agent_window(self._cutoff(None))

    def agent_history_window(self, *, include_bars: bool = True) -> Dict[str, Any]:
        """Owner-only bounded raw-trade/minute-bar tail; no future access."""
        return self._history.agent_window(self._cutoff(None), include_bars=include_bars)

    def capture_agent_history(self, *, include_bars: bool = True) -> HistoryCapture:
        """Owner-only bounded row references; materialization happens on the query worker."""
        return self._history.capture_agent_window(self._cutoff(None), include_bars=include_bars)
