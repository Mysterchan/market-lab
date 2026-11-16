"""Single-owner cooperative playback. All queries see only committed events."""

from __future__ import annotations
import logging

from collections import deque
from dataclasses import asdict, replace
import json
from pathlib import Path
from typing import Any, Callable, Deque, Dict, Iterable, Iterator, List, Optional, Tuple

from sim_platform.resources import cleanup

from .feed import BinaryFileFeed
from .models import (
    DataUnavailable,
    FeedRecord,
    FutureDataError,
    MarketError,
    MarketEvent,
    MarketSnapshot,
    MarketTrade,
    NewsRecord,
    time_ns,
)
from .native import NativeEngine, PathInput

_log = logging.getLogger(__name__)

Subscriber = Callable[[MarketEvent], Any]


class MarketSession:
    """One symbol's market, rebuilt event by event from a source file or stream.

    The session owns the native order book. Each step applies one source message or
    delivers one publication, updates ``snapshot`` and calls the subscribers. Only one
    caller drives it: an execution session may claim it so nothing else can advance it.

    Lifecycle: ``unloaded`` → ``loading`` → ``ready`` → ``playing`` / ``paused`` →
    ``completed``, ``failed`` or ``closed``.

    Args:
        symbol: 1 to 8 uppercase ASCII characters.
        depth: Price levels per side kept in each snapshot, 1 to 100.
        library: Path to the compiled adapter; by default the one under ``build/``.
    """

    def __init__(
        self, symbol: str = 'AAPL', *, depth: int = 5, library: Optional[PathInput] = None
    ) -> None:
        if (
            not isinstance(symbol, str)
            or not symbol
            or len(symbol) > 8
            or not symbol.isascii()
            or symbol != symbol.strip().upper()
        ):
            raise MarketError('symbol must be 1..8 uppercase ASCII characters')
        if type(depth) is not int or not 1 <= depth <= 100:
            raise MarketError('depth must be 1..100')
        self._symbol, self._depth, self._library = symbol, depth, library
        self._snapshot = MarketSnapshot(symbol, 0, 0, 'unloaded', 'unavailable')
        self._engine: Any = None
        self._feed: Any = None
        # Record iterator of a stream source; a file source is read by the engine.
        self._records: Iterator[FeedRecord] = iter(())
        # The next source record, peeked but not yet applied.
        self._pending: Any = None
        # Publications in delivery order, and how many have been delivered.
        self._news: Tuple[NewsRecord, ...] = ()
        self._news_index = 0
        self._callbacks: List[Subscriber] = []
        self._notifying = False
        self._execution_owner: Optional[object] = None
        self._subscriber_errors: Deque[str] = deque(maxlen=100)
        # A source that never mentions the symbol is an error, not an empty market.
        self._seen_symbol = False

    @property
    def snapshot(self) -> MarketSnapshot:
        return self._snapshot

    @property
    def status(self) -> str:
        return self._snapshot.status

    @property
    def time_ns(self) -> int:
        return self._snapshot.time_ns

    @property
    def subscriber_errors(self) -> Tuple[str, ...]:
        return tuple(self._subscriber_errors)

    @property
    def is_notifying(self) -> bool:
        return self._notifying

    @property
    def has_execution_owner(self) -> bool:
        return self._execution_owner is not None

    def claim_execution(self, owner: object) -> None:
        self._control()
        if self.has_execution_owner:
            raise MarketError('market already has an execution owner')
        self._execution_owner = owner

    def release_execution(self, owner: object) -> None:
        if self._execution_owner is owner:
            self._execution_owner = None

    def subscribe(self, callback: Subscriber) -> Callable[[], None]:
        """Call ``callback`` with every event; the returned function unsubscribes it."""
        if not callable(callback):
            raise MarketError('subscriber must be callable')
        self._callbacks.append(callback)

        def unsubscribe() -> None:
            if callback in self._callbacks:
                self._callbacks.remove(callback)

        return unsubscribe

    def _emit(
        self,
        kind: str,
        source_type: str = '',
        trade: Optional[MarketTrade] = None,
        news: Optional[NewsRecord] = None,
        message: Optional[str] = None,
        broken_match_id: Optional[int] = None,
    ) -> MarketEvent:
        """Build an event for the current snapshot and deliver it to every subscriber.

        A subscriber's exception is recorded and does not stop the others.
        """
        event = MarketEvent(
            kind,
            self.time_ns,
            self.snapshot.sequence,
            source_type,
            self.snapshot,
            trade,
            news,
            message,
            broken_match_id,
        )
        self._notifying = True
        try:
            for callback in tuple(self._callbacks):
                try:
                    callback(event)
                except Exception as exc:
                    _log.warning('Handled failure in _emit', exc_info=True)
                    self._subscriber_errors.append(str(exc))
        finally:
            self._notifying = False
        return event

    def _control(self) -> None:
        if self._notifying:
            raise MarketError('session controls are forbidden inside subscribers')

    def _state(self, status: str) -> MarketEvent:
        """Change the playback status and publish it."""
        self._snapshot = replace(self.snapshot, status=status)
        return self._emit('status')

    def load(
        self,
        path: PathInput,
        *,
        news: Iterable[NewsRecord] = (),
        allow_implicit_eof: bool = False,
    ) -> MarketSnapshot:
        """Open a BinaryFILE recording; the session becomes ``ready`` at time zero.

        Raises:
            MarketError: If the session was already loaded or the source is invalid.
        """
        self._control()
        if self.status != 'unloaded':
            raise MarketError('load requires a new session')
        self._state('loading')
        try:
            self._prepare(BinaryFileFeed(path, allow_implicit_eof), news)
        except Exception as exc:
            self._fail(str(exc))
            raise MarketError(str(exc)) from exc
        return self.snapshot

    def load_stream(self, feed: Any, *, news: Iterable[NewsRecord] = ()) -> MarketSnapshot:
        """Use a sequenced in-memory feed instead of a file."""
        self._control()
        if self.status != 'unloaded':
            raise MarketError('load requires a new session')
        self._state('loading')
        try:
            self._prepare(feed, news)
        except Exception as exc:
            self._fail(str(exc))
            raise MarketError(str(exc)) from exc
        return self.snapshot

    def _prepare(self, feed: Any, news: Iterable[NewsRecord]) -> None:
        """Validate the publications, create the native engine and bind the source."""
        self._feed = feed
        records = tuple(news)
        ids = set()
        for item in records:
            if not isinstance(item, NewsRecord):
                raise MarketError('news must contain NewsRecord values')
            time_ns(item.available_time_ns)
            if not all(
                isinstance(v, str) and v
                for v in (item.record_id, item.period, item.headline, item.provenance)
            ):
                raise MarketError('news fields must be nonempty text')
            if (
                type(item.revision) is not int
                or item.revision < 1
                or (item.record_id, item.revision) in ids
            ):
                raise MarketError('invalid or duplicate news revision')
            ids.add((item.record_id, item.revision))
        self._news = tuple(
            sorted(records, key=lambda n: (n.available_time_ns, n.record_id, n.revision))
        )
        self._engine = NativeEngine(self._symbol, self._library, self._depth)
        if isinstance(feed, BinaryFileFeed):
            feed.bind(self._engine)
        else:
            self._records = iter(feed)
        self._snapshot = replace(self.snapshot, integrity=feed.integrity)
        self._state('ready')

    def source_descriptor(self) -> Optional[Dict[str, Any]]:
        """Describe the source and publication inputs; perform no checksum IO."""
        if not isinstance(self._feed, BinaryFileFeed):
            return None
        path = Path(self._feed.path).expanduser().resolve()
        return dict(
            path=str(path),
            depth=self._depth,
            native_path=str(self._engine.library_path),
            allow_implicit_eof=self._feed.allow_implicit_eof,
            news=[asdict(item) for item in self._news],
        )

    def play(self) -> MarketEvent:
        self._control()
        if self.status not in ('ready', 'paused'):
            raise MarketError('play requires ready or paused session')
        return self._state('playing')

    def pause(self) -> MarketEvent:
        self._control()
        if self.status not in ('ready', 'playing', 'paused'):
            raise MarketError('pause requires active session')
        return self._state('paused')

    def step(self) -> MarketEvent:
        """Advance by exactly one event from a ready or paused session."""
        self._control()
        if self.status not in ('ready', 'paused'):
            raise MarketError('step requires ready or paused session')
        if self.status == 'ready':
            self._state('paused')
        return self._advance()

    def pump(self, count: int = 1) -> Tuple[MarketEvent, ...]:
        """Host loop calls pump while playing; pause makes it a no-op."""
        self._control()
        if type(count) is not int or count < 1:
            raise MarketError('pump count must be a positive integer')
        events = []
        for _ in range(count):
            if self.status != 'playing':
                break
            events.append(self._advance())
        return tuple(events)

    def _peek(self) -> Any:
        """Return the next source record without applying it; None at the end.

        A file source returns the record's metadata; a stream returns the FeedRecord.
        """
        if self._pending is None:
            if isinstance(self._feed, BinaryFileFeed):
                self._pending = self._feed.peek()
            else:
                self._pending = next(self._records, None)
        return self._pending

    def _next_news(self) -> Optional[NewsRecord]:
        """The next publication not yet delivered, or None."""
        if self._news_index < len(self._news):
            return self._news[self._news_index]
        return None

    @property
    def next_event_time_ns(self) -> Optional[int]:
        """Peek the next publication/source time without changing visible state."""
        if self.status not in ('ready', 'paused', 'playing'):
            return None
        record = self._peek()
        stamp = None
        if record is not None:
            stamp = (
                record['timestamp_ns']
                if isinstance(self._feed, BinaryFileFeed)
                else self._engine.inspect(record.payload, record.sequence)['timestamp_ns']
            )
        news = self._next_news()
        if news is not None:
            stamp = news.available_time_ns if stamp is None else min(stamp, news.available_time_ns)
        return stamp

    def _locate(self, record: Any) -> Tuple[int, int]:
        """Return (sequence, time) of the next source record after validating its order."""
        if isinstance(self._feed, BinaryFileFeed):
            metadata = record
            sequence = metadata['sequence']
        else:
            if not isinstance(record, FeedRecord) or type(record.sequence) is not int:
                raise MarketError('invalid feed record')
            if not isinstance(record.payload, bytes):
                raise MarketError('invalid ITCH payload')
            metadata, sequence = None, record.sequence
        if sequence != self.snapshot.sequence + 1:
            raise MarketError('feed sequence gap, duplicate or out-of-order record')
        if metadata is None:
            metadata = self._engine.inspect(record.payload, sequence)
        stamp = time_ns(metadata['timestamp_ns'])
        if stamp < self.time_ns:
            raise MarketError('feed timestamp moved backwards')
        return sequence, stamp

    def _apply(self, record: Any, sequence: int, stamp: int) -> MarketEvent:
        """Apply one source record to the native book and publish the new snapshot."""
        result = (
            self._feed.advance()
            if isinstance(self._feed, BinaryFileFeed)
            else self._engine.apply(record.payload, sequence)
        )
        kind = result['source_type']
        # False for a message about another symbol; such a message only moves time.
        selected = result['selected']
        trade = None
        if selected and result['trade']:
            trade = MarketTrade(sequence, stamp, kind=kind, **result['trade'])
        # ITCH type B reports that an earlier trade was broken.
        broken = result['broken_match_id'] if selected and kind == 'B' else None
        updates: Dict[str, Any] = dict(time_ns=stamp, sequence=sequence)
        if result['book']:
            updates.update(result['book'])
            updates['bids'] = tuple(tuple(v) for v in updates['bids'])
            updates['asks'] = tuple(tuple(v) for v in updates['asks'])
            updates['book_time_ns'] = stamp
        if result['trading_state'] is not None:
            updates['trading_state'] = result['trading_state']
        if result['system_event'] is not None:
            updates['system_event'] = result['system_event']
        self._snapshot = replace(self.snapshot, **updates)
        if selected:
            self._seen_symbol = True
        self._pending = None
        # ITCH type S is a system event; it applies to every symbol.
        return self._emit(
            'market' if selected or kind == 'S' else 'clock',
            kind,
            trade,
            broken_match_id=broken,
        )

    def _advance(self) -> MarketEvent:
        """Deliver the next event: a due publication, a source record, or completion.

        Any error fails the session and is returned as an ``integrity`` event.
        """
        try:
            record = self._peek()
            sequence, stamp = self._locate(record) if record is not None else (None, None)
            # Publications precede ITCH at ties; ITCH order itself is preserved.
            news = self._next_news()
            if news is not None and (stamp is None or news.available_time_ns <= stamp):
                self._news_index += 1
                self._snapshot = replace(self.snapshot, time_ns=news.available_time_ns)
                return self._emit('news', news=news)
            if record is None or sequence is None or stamp is None:
                if not self._seen_symbol:
                    raise MarketError('requested symbol never appeared in input')
                self._release()
                return self._state('completed')
            return self._apply(record, sequence, stamp)
        except Exception as exc:
            _log.warning('Handled failure in _advance', exc_info=True)
            return self._fail(str(exc))

    def status_event(self) -> MarketEvent:
        return MarketEvent('status', self.time_ns, self.snapshot.sequence, '', self.snapshot)

    def fail(self, message: str) -> MarketEvent:
        """Mark the session as failed, for example when its history cannot be stored."""
        self._control()
        return self._fail(message)

    def _fail(self, message: str) -> MarketEvent:
        self._snapshot = replace(
            self.snapshot, status='failed', integrity='desynchronized', error=message
        )
        self._release()
        return self._emit('integrity', message=message)

    def _release(self) -> None:
        """Close the native engine and the source."""
        cleanup(
            *(resource.close for resource in (self._engine, self._feed) if resource is not None)
        )

    def close(self) -> None:
        self._control()
        self._release()
        if self.status not in ('failed', 'completed', 'closed'):
            self._state('closed')

    def __enter__(self) -> MarketSession:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def query_cutoff(self, as_of_ns: Optional[int]) -> int:
        """Resolve a query time: None means now, and a future time is refused."""
        cutoff = self.time_ns if as_of_ns is None else time_ns(as_of_ns)
        if cutoff > self.time_ns:
            raise FutureDataError('query time is after simulation time')
        if self.status not in ('ready', 'playing', 'paused', 'completed'):
            raise DataUnavailable(self.snapshot.error or 'market data is unavailable')
        return cutoff

    def book(self, *, as_of_ns: Optional[int] = None) -> MarketSnapshot:
        """Return the current book; only the latest book is kept, not past ones."""
        cutoff = self.query_cutoff(as_of_ns)
        if cutoff != self.time_ns:
            raise DataUnavailable(
                'historical book query is unsupported; only current book is retained'
            )
        if self.snapshot.book_time_ns is None:
            raise DataUnavailable('no order-book event observed yet')
        return self.snapshot


def load_news(path: PathInput) -> Tuple[NewsRecord, ...]:
    """Fixture publication time is user-declared, not provider-verified."""
    values = json.loads(Path(path).read_text())
    if not isinstance(values, list):
        raise MarketError('news fixture must be a JSON list')
    try:
        return tuple(NewsRecord(**value) for value in values)
    except TypeError as exc:
        raise MarketError('invalid news fixture fields') from exc
