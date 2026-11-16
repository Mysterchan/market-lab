"""One cursor over an immutable prepared source; no native decoding during Play.

Import service is injected by Platform. Each cursor owns its reader mapping and
optional import job; immutable files are retained by the workspace inventory.
"""

from __future__ import annotations
import logging

from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from sim_platform.domain.json_values import copy_json
from sim_platform.domain.models import ContractError, integer
from sim_platform.market.models import (
    DataUnavailable,
    FutureDataError,
    MarketError,
    MarketEvent,
    MarketSnapshot,
    MarketTrade,
    NewsRecord,
    PriceVolumeBar,
)
from sim_platform.market.native import PathInput
from sim_platform.market.pacing import PlaybackSchedule
from sim_platform.market.window import INTERVAL

from .publications import PublicationReader
from .reader import PreparedMarket

_log = logging.getLogger(__name__)

Subscriber = Callable[[MarketEvent], Any]

# 09:30:00 Eastern Time, the start of the regular trading session.
REGULAR_OPEN_NS = 34_200_000_000_000
_ERROR_LIMIT = 100


# Manifest values that a re-preparation of the same source must reproduce exactly.
PREPARED_SHAPE = ('symbol', 'depth', 'frames', 'source_frames', 'states', 'first_ns', 'last_ns')


class PreparedSession:
    """A playback cursor over a prepared market, used by research experiments.

    It offers the same play, pause, step and query interface as a native
    MarketSession, but every step only reads the next prepared event; no book is
    rebuilt during playback.

    ``load()`` starts or reuses a preparation, ``prepare()`` polls it until the cursor
    is ``ready``, and ``seek()`` or ``start_position()`` chooses where the experiment
    begins. After an execution session claims the cursor, only that owner advances it.

    Args:
        imports: Service that prepares a source or returns an existing artifact.
        symbol: The stock to read.
        depth: Price levels per side kept in each snapshot.
        library: Path to the compiled adapter used for preparation.
    """

    # True while subscribers are being called; controls are refused until they return.
    is_notifying = False

    def __init__(
        self,
        imports: Any,
        symbol: str = 'AAPL',
        *,
        depth: int = 5,
        library: Optional[PathInput] = None,
        bar_spread: Optional[int] = None,
        bar_origin: Optional[str] = None,
    ) -> None:
        self._imports, self._symbol, self._depth, self._library = imports, symbol, depth, library
        # A price-bar recording's synthetic spread; None selects the default.
        self._bar_spread = bar_spread
        # A price-bar recording's market day 0, when shared with other recordings.
        self._bar_origin = bar_origin
        # A PreparedMarket, or a PublicationReader when publications are merged in.
        self._reader: Any = None
        # The running preparation, until its artifact is attached.
        self._job: Any = None
        self._owner: Optional[object] = None
        self._news: Tuple[NewsRecord, ...] = ()
        self._snapshot = MarketSnapshot(symbol, 0, 0, 'unloaded', 'unavailable')
        # Number of events of the reader's timeline that this cursor has delivered.
        self._index = 0
        self._origin_ns = 0
        self._closed = False
        self._on_close: Callable[[], None] = lambda: None
        self._playback = PlaybackSchedule(self)
        self._callbacks: List[Subscriber] = []
        self._errors: List[str] = []

    @classmethod
    def from_reader(cls, reader: Any, start_ns: int = 0) -> PreparedSession:
        """Take ownership of a trusted reader lease; used by operation Replay."""
        session = cls(None, reader.manifest['symbol'], depth=reader.manifest['depth'])
        session._reader = reader
        session._snapshot = replace(reader.snapshot_at(0), status='ready')
        session.seek(start_ns)
        return session

    @property
    def origin_ns(self) -> int:
        """The market time the experiment started from."""
        return self._origin_ns

    @property
    def merged_prefix(self) -> int:
        """Number of events delivered so far, counting publications."""
        return self._index

    @property
    def reader(self) -> Any:
        if self._reader is None:
            raise DataUnavailable('prepared source is not ready')
        return self._reader

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
    def has_execution_owner(self) -> bool:
        return self._owner is not None

    @property
    def subscriber_errors(self) -> Tuple[str, ...]:
        return tuple(self._errors)

    def _check(self) -> None:
        if self._closed or self.is_notifying:
            raise MarketError('prepared session is closed or publishing callbacks')

    def on_close(self, callback: Callable[[], None]) -> None:
        self._on_close = callback

    def claim_execution(self, owner: object) -> None:
        self._check()
        if self._owner is not None:
            raise ContractError('prepared cursor already has an execution owner')
        self._owner = owner

    def release_execution(self, owner: object) -> None:
        if self._owner is owner:
            self._owner = None

    def source_descriptor(self) -> Optional[Dict[str, Any]]:
        """Describe the source so Platform.replay_run() can prepare it again.

        Replay rebuilds the prepared artifact from the same file with the same options
        and checks that it has the same shape, instead of reading this artifact. A
        cursor that is not attached to an artifact has nothing to describe.
        """
        if self._reader is None:
            return None
        m = self.reader.manifest
        book = (m.get('calendar') or {}).get('synthetic_book')
        return dict(
            kind='prepared',
            path=m['source'],
            source_identity=copy_json(m['source_identity']),
            depth=m['depth'],
            bar_spread=book['spread'] if book else None,
            bar_origin=self._bar_origin,
            allow_implicit_eof=m['allow_implicit_eof'],
            news=[asdict(n) for n in self._news],
            shape={key: m[key] for key in PREPARED_SHAPE},
        )

    @property
    def calendar(self) -> Optional[Dict[str, Any]]:
        """How market time maps to dates, for a recording prepared from price bars.

        None for a native one-day recording, whose file carries no date.
        """
        return self.reader.manifest.get('calendar')

    def prepared_descriptor(self) -> Dict[str, Any]:
        """Identify the artifact this cursor reads, for a later recorded replay."""
        m = self.reader.manifest
        descriptor = dict(
            artifact=str(self.reader.directory),
            source_frames=m['source_frames'],
            frames=m['frames'],
            depth=m['depth'],
        )
        # Saved with the run, so every view and report can show dates without the artifact.
        if 'calendar' in m:
            descriptor['calendar'] = m['calendar']
        return descriptor

    def load(
        self,
        path: PathInput,
        *,
        news: Iterable[NewsRecord] = (),
        allow_implicit_eof: bool = False,
    ) -> MarketSnapshot:
        """Begin using a source file; returns at once with status ``loading`` or ``ready``.

        An already prepared source is attached immediately. Otherwise preparation
        starts in the background; call ``prepare()`` until it returns True.
        """
        self._check()
        if self.status != 'unloaded':
            raise MarketError('load requires a fresh prepared cursor')
        self._news = tuple(news)
        try:
            result = self._imports.begin(
                path,
                symbol=self._symbol,
                depth=self._depth,
                allow_implicit_eof=allow_implicit_eof,
                library=self._library,
                bar_spread=self._bar_spread,
                bar_origin=self._bar_origin,
            )
            self._snapshot = replace(self._snapshot, status='loading')
            if isinstance(result, Path):
                self._attach(result)
            else:
                self._job = result
        except Exception as exc:
            self.fail(str(exc))
            raise
        return self.snapshot

    def _attach(self, path: Path) -> None:
        """Open the prepared artifact and position the cursor at time zero."""
        reader: Any = PreparedMarket(path)
        try:
            if self._news:
                reader = PublicationReader(reader, self._news)
            if reader.manifest['symbol'] != self._symbol or reader.manifest['depth'] != self._depth:
                raise ContractError('prepared source settings differ')
            book = (reader.manifest.get('calendar') or {}).get('synthetic_book')
            if self._bar_spread is not None and (book or {}).get('spread') != self._bar_spread:
                raise ContractError('prepared source settings differ')
            self._snapshot = replace(reader.snapshot_at(0), status='ready')
        except Exception:
            reader.close()
            raise
        self._reader, self._index = reader, reader.prefix(0)

    def prepare(self) -> bool:
        """Poll import without blocking on the full source; return ready boolean."""
        self._check()
        if self._reader is not None:
            return True
        if self._job is None:
            raise ContractError('no prepared import is active')
        try:
            path = self._job.result(timeout=0)
        except TimeoutError:
            return False
        except Exception as exc:
            self.fail(str(exc))
            raise
        self._attach(path)
        self._job = None
        return True

    def preparation_progress(self) -> Dict[str, Any]:
        return self._job.snapshot() if self._job else dict(frames=0, alive=False)

    def seek(self, time_ns: int) -> MarketSnapshot:
        """Select initial prefix only before a financial owner binds this cursor."""
        self._check()
        integer(time_ns, 'start time')
        if self.has_execution_owner or self.status not in ('ready', 'paused'):
            raise ContractError('seek requires an unbound ready prepared cursor')
        if time_ns > self.reader.manifest['last_ns']:
            raise DataUnavailable('start exceeds prepared source coverage')
        self._origin_ns = time_ns
        self._snapshot = replace(self.reader.snapshot_at(time_ns), status='paused')
        self._index = self.reader.prefix(time_ns)
        return self.snapshot

    def start_position(self, mode: str, time_ns: Optional[int] = None) -> MarketSnapshot:
        """Move to a named starting point.

        ``beginning`` is time zero, ``first_book`` the first moment with both a bid and
        an ask, ``regular`` the 09:30 open, and ``custom`` the given ``time_ns``.
        """
        if mode == 'beginning':
            return self.seek(0)
        if mode == 'first_book':
            time_ns = self.reader.manifest['history']['first_book_ns']
            if time_ns is None:
                raise DataUnavailable('source never contains a two-sided book')
        elif mode == 'regular':
            time_ns = REGULAR_OPEN_NS
        elif mode != 'custom':
            raise ContractError('unknown experiment start mode')
        if time_ns is None:
            raise ContractError('a custom start needs a time')
        return self.seek(time_ns)

    def subscribe(self, callback: Subscriber) -> Callable[[], None]:
        self._check()
        if not callable(callback):
            raise MarketError('subscriber must be callable')
        self._callbacks.append(callback)

        def unsubscribe() -> None:
            if callback in self._callbacks:
                self._callbacks.remove(callback)

        return unsubscribe

    def _emit(self, event: MarketEvent) -> MarketEvent:
        if not self._callbacks:
            return event
        self.is_notifying = True
        try:
            for callback in tuple(self._callbacks):
                try:
                    callback(event)
                except Exception as exc:
                    _log.warning('Handled failure in _emit', exc_info=True)
                    self._errors = (self._errors + [str(exc)])[-_ERROR_LIMIT:]
        finally:
            self.is_notifying = False
        return event

    def status_event(self) -> MarketEvent:
        return MarketEvent('status', self.time_ns, self.snapshot.sequence, '', self.snapshot)

    def play(self, speed: Optional[float] = None) -> MarketEvent:
        self._check()
        if self.status not in ('ready', 'paused'):
            raise MarketError('play requires a ready prepared cursor')
        self._playback.play(speed)
        self._snapshot = replace(self.snapshot, status='playing')
        return self._emit(self.status_event())

    def pause(self) -> MarketEvent:
        self._check()
        if self.status not in ('ready', 'paused', 'playing'):
            raise MarketError('pause requires an active prepared cursor')
        self._playback.pause()
        self._snapshot = replace(self.snapshot, status='paused')
        return self._emit(self.status_event())

    @property
    def next_event_time_ns(self) -> Optional[int]:
        if self._reader is None or self._index >= self.reader.manifest['frames']:
            return None
        return self.reader.event_time(self._index)

    def _advance(self) -> MarketEvent:
        """Deliver the next prepared event, or mark the cursor completed at the end."""
        if self._index >= self.reader.manifest['frames']:
            self._snapshot = replace(self.snapshot, status='completed')
            return self._emit(self.status_event())
        event = self.reader.event(self._index)
        self._index += 1
        if event.snapshot.status != self.status:
            event = replace(event, snapshot=replace(event.snapshot, status=self.status))
        self._snapshot = event.snapshot
        return self._emit(event)

    def idle_candidate(self, end_prefix: int) -> Optional[MarketEvent]:
        """Owner-only certified endpoint; observers require ordinary events."""
        self._check()
        if self._owner is None or self.status != 'playing' or self._callbacks:
            return None
        if not self._index < end_prefix <= self.reader.idle_span_end(self._index):
            raise ContractError('idle advance exceeds stable source span')
        return self.reader.event(end_prefix - 1)

    def apply_idle(self, end_prefix: int, event: MarketEvent) -> None:
        """Jump the cursor to ``end_prefix`` after the owner proved nothing can fill."""
        if (
            self._owner is None
            or self.status != 'playing'
            or self._callbacks
            or not self._index < end_prefix <= self.reader.idle_span_end(self._index)
            or event.sequence != end_prefix
        ):
            raise ContractError('idle publication differs from the certified source prefix')
        self._index, self._snapshot = end_prefix, event.snapshot

    def step(self) -> MarketEvent:
        self._check()
        if self.status not in ('ready', 'paused'):
            raise MarketError('step requires a paused prepared cursor')
        self._snapshot = replace(self.snapshot, status='paused')
        return self._advance()

    def pump(self, count: int = 1) -> Tuple[MarketEvent, ...]:
        self._check()
        integer(count, 'pump count', 1)
        values = []
        for _ in range(count):
            if self.status != 'playing':
                break
            values.append(self._advance())
        return tuple(values)

    def poll(self, *, max_events: int = 1, budget_seconds: Optional[float] = None) -> None:
        self._playback.poll(self.pump, max_events=max_events, budget_seconds=budget_seconds)

    def query_cutoff(self, as_of_ns: Optional[int] = None) -> int:
        self._check()
        target = self.time_ns if as_of_ns is None else as_of_ns
        integer(target, 'query time')
        if target > self.time_ns:
            raise FutureDataError('query exceeds published cursor time')
        return target

    def book(self, *, as_of_ns: Optional[int] = None) -> MarketSnapshot:
        cutoff = self.query_cutoff(as_of_ns)
        return self.reader.snapshot_at(cutoff)

    def trades(
        self,
        *,
        as_of_ns: Optional[int] = None,
        start_ns: int = 0,
        include_nonprintable: bool = False,
        limit: Optional[int] = None,
    ) -> Tuple[MarketTrade, ...]:
        cutoff = self.query_cutoff(as_of_ns)
        return self.reader.history.trades(
            min(self._index, self.reader.prefix(cutoff)),
            cutoff,
            start_ns=start_ns,
            limit=limit,
            include_nonprintable=include_nonprintable,
        )

    def bars(
        self,
        interval_ns: int = INTERVAL,
        *,
        as_of_ns: Optional[int] = None,
        start_ns: int = 0,
        limit: Optional[int] = None,
    ) -> Tuple[PriceVolumeBar, ...]:
        cutoff = self.query_cutoff(as_of_ns)
        return self.reader.history.bars(
            min(self._index, self.reader.prefix(cutoff)),
            cutoff,
            start_ns=start_ns,
            limit=limit,
            interval_ns=interval_ns,
        )

    def news(
        self, *, as_of_ns: Optional[int] = None, limit: Optional[int] = None
    ) -> Tuple[NewsRecord, ...]:
        cutoff = self.query_cutoff(as_of_ns)
        if hasattr(self.reader, 'news_for_prefix'):
            return self.reader.news_for_prefix(min(self._index, self.reader.prefix(cutoff)), limit)
        return ()

    def prepare_agent_history(self) -> None:
        # History is prepared at import, not rebuilt per run; reading the property only
        # checks that a prepared source is bound.
        self.reader  # noqa: B018

    def capture_agent_history(self, *, include_bars: bool = True) -> Dict[str, Any]:
        return self.reader.history.capture(self._index, self.time_ns, include_bars=include_bars)

    def agent_history_window(self, *, include_bars: bool = True) -> Dict[str, Any]:
        return self.capture_agent_history(include_bars=include_bars)

    def fail(self, reason: str) -> MarketEvent:
        self._snapshot = replace(
            self.snapshot, status='failed', integrity='unavailable', error=reason
        )
        return self._emit(self.status_event())

    def close(self) -> None:
        if self._closed:
            return
        if self._owner is not None:
            raise ContractError('close the execution owner before its prepared cursor')
        if self._job is not None:
            self._job.close()
            self._job = None
        if self._reader is not None:
            self._reader.close()
        self._snapshot = replace(self.snapshot, status='closed')
        self._closed = True
        self._on_close()

    def __enter__(self) -> PreparedSession:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
