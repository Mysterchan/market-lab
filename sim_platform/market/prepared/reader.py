"""Trusted indexed reads; visible facade fixes cutoffs without native reconstruction."""

from __future__ import annotations

import json
import mmap
import os
from pathlib import Path
from threading import RLock
from typing import Any, BinaryIO, Callable, Dict, List, Optional, Sequence, Tuple

from sim_platform.domain.models import ContractError, integer
from sim_platform.market.models import (
    MAX_TIME_NS,
    DataUnavailable,
    FutureDataError,
    MarketEvent,
    MarketSnapshot,
)
from sim_platform.market.native import PathInput

from .codec import EVENT, STATE_HEADER_PREFIX, decode_event, decode_state, state_size
from .history import BAR, BUCKET, TRADE, PreparedHistory
from .spans import BOOK_EVENTS, SPAN, StableSpans

_MANIFEST_LIMIT = 65536
_MAX_DEPTH = 100
# The three flag bits of an event record; any higher bit means a corrupt record.
_EVENT_FLAG_MASK = 7
_CANCEL_CHECK_EVENTS = 8192
# The state before any event: time zero, no source frame, state version zero.
_EMPTY_ROW = (0, 0, 0)


class PreparedMarket:
    """Read-only access to one prepared artifact directory.

    The artifact's files are memory-mapped and never modified. Positions are given as
    a ``prefix``: the number of prepared events that have occurred. Prefix 0 is the
    empty market before the first event; the snapshot at prefix N is the market after
    event N. Methods are safe to call from several threads.

    Opening checks the manifest and file sizes only. Pass ``verify=True`` or call
    ``validate()`` for a full audit of the contents.
    """

    def _read_manifest(self) -> dict:
        with (self.directory / 'manifest.json').open() as stream:
            raw = stream.read(_MANIFEST_LIMIT + 1)
        if len(raw) > _MANIFEST_LIMIT:
            raise ContractError('prepared manifest too large')
        value = json.loads(raw)
        fields = {
            'symbol',
            'depth',
            'frames',
            'source_frames',
            'states',
            'first_ns',
            'last_ns',
            'source',
            'source_identity',
            'allow_implicit_eof',
            'coverage_end_ns',
            'profile',
            'state_bytes',
            'files',
            'history',
            'stable_spans',
            'complete',
        }
        # A bar source also records its calendar; a native import has no such entry.
        if (
            type(value) is not dict
            or set(value) - {'calendar'} != fields
            or value['complete'] is not True
        ):
            raise ContractError('unsupported or incomplete prepared market; rebuild required')
        return value

    def _validate_metadata(self) -> None:
        """Check counts, symbol, fixed record layout and source coverage."""
        metadata = self.manifest
        for key in ('frames', 'source_frames', 'states', 'depth', 'stable_spans'):
            integer(metadata[key], key, 1)
        symbol = metadata['symbol']
        if (
            not isinstance(symbol, str)
            or not 1 <= len(symbol) <= 8
            or not symbol.isascii()
            or symbol != symbol.strip().upper()
        ):
            raise ContractError('invalid prepared symbol')
        if (
            metadata['states'] > metadata['frames'] + 1
            or metadata['depth'] > _MAX_DEPTH
            or metadata['state_bytes'] != state_size(metadata['depth'])
        ):
            raise ContractError('invalid prepared depth/layout')
        integer(metadata['first_ns'], 'first time')
        integer(metadata['last_ns'], 'last time', metadata['first_ns'])
        if metadata['last_ns'] >= MAX_TIME_NS or metadata['frames'] > metadata['source_frames']:
            raise ContractError('invalid prepared source bounds')

    def _map_file(self, name: str, size: int) -> Any:
        """Own each handle before validation so constructor rollback closes it."""
        stream = (self.directory / name).open('rb')
        self._files.append(stream)
        stat = os.fstat(stream.fileno())
        if stat.st_size != size:
            raise ContractError('prepared file length mismatch')
        if self.manifest['files'][name] != dict(bytes=size, mtime_ns=stat.st_mtime_ns):
            raise ContractError('prepared file changed since validation; rebuild required')
        mapping = mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) if size else b''
        self._maps.append(mapping)
        return mapping

    def _open_events(self) -> None:
        metadata = self.manifest
        self._map_file('events.bin', metadata['frames'] * EVENT.size)
        self._map_file('states.bin', metadata['states'] * metadata['state_bytes'])
        self._snapshot(_EMPTY_ROW, 0)
        self._validate_row(0)
        last = self._validate_row(metadata['frames'] - 1)
        if last[2] + 1 != metadata['states']:
            raise ContractError('prepared state count mismatch')

    def _open_history(self) -> None:
        """Validate history counts and map its three indexed data files."""
        history = self.manifest['history']
        fields = {
            'trades',
            'bar_versions',
            'buckets',
            'interval_ns',
            'first_book_ns',
            'peak_events_per_second',
            'peak_second_ns',
        }
        if type(history) is not dict or set(history) != fields:
            raise ContractError('unsupported prepared history; rebuild required')
        for key in ('trades', 'bar_versions', 'buckets'):
            integer(history[key], key)
        self._map_file('trades.bin', history['trades'] * TRADE.size)
        self._map_file('bar-versions.bin', history['bar_versions'] * BAR.size)
        self._map_file('bar-index.bin', history['buckets'] * BUCKET.size)
        trades, bar_versions, bar_index = self._maps[2:5]
        self.history = PreparedHistory(trades, bar_versions, bar_index, history)

    def _open_spans(self) -> None:
        count = self.manifest['stable_spans']
        mapping = self._map_file('spans.bin', count * SPAN.size)
        self.spans = StableSpans(mapping, count)

    def __init__(
        self,
        directory: PathInput,
        *,
        verify: bool = False,
        cancelled: Callable[[], bool] = lambda: False,
    ) -> None:
        self.directory = Path(directory).resolve()
        self._files: List[BinaryIO] = []
        # Mapped in a fixed order: events, states, trades, bar versions, bar index, spans.
        self._maps: List[Any] = []
        self._closed = False
        self._lock = RLock()
        try:
            self.manifest = self._read_manifest()
            self._validate_metadata()
            self._open_events()
            self._open_history()
            self._open_spans()
            if verify:
                self.validate(cancelled=cancelled)
        except Exception:
            self.close()
            raise

    def _check(self) -> None:
        if self._closed:
            raise ContractError('prepared reader closed')

    def _row(self, index: int) -> Tuple[Any, ...]:
        """Unpack one event record and reject impossible field values."""
        row = EVENT.unpack_from(self._maps[0], index * EVENT.size)
        if (
            not 0 < row[1] <= self.manifest['source_frames']
            or not 0 <= row[2] < self.manifest['states']
            or row[3] != b'M'
            or row[8] > _EVENT_FLAG_MASK
            or not self.manifest['first_ns'] <= row[0] <= self.manifest['last_ns']
        ):
            raise ContractError('corrupt prepared event index')
        return row

    def _validate_row(self, index: int) -> Tuple[Any, ...]:
        row = self._row(index)
        self._snapshot(row, index + 1)
        return row

    def validate(self, *, cancelled: Callable[[], bool] = lambda: False) -> None:
        """Explicit O(events + state versions*depth) audit of a local artifact.

        Normal open trusts the completed importer and unchanged file metadata;
        this is accidental-change detection, not authentication of hostile files.
        No checksum, source replay or native book reconstruction is performed.
        """
        with self._lock:
            self._check()
            previous_time, previous_source, version = -1, 0, 0
            span_index = 0
            span_start, span_end, span_key = 0, SPAN.unpack_from(self.spans.mapping, 0)[0], None
            for index in range(self.manifest['frames']):
                if index % _CANCEL_CHECK_EVENTS == 0 and cancelled():
                    from .builder import PreparationCancelled

                    raise PreparationCancelled('market preparation cancelled')
                row = self._row(index)
                if index == span_end:
                    span_index += 1
                    span_start, span_end, span_key = (
                        index,
                        SPAN.unpack_from(self.spans.mapping, span_index * SPAN.size)[0],
                        None,
                    )
                if span_end - span_start > 1:
                    if row[4] not in BOOK_EVENTS or row[8]:
                        raise ContractError('stable span contains a financial publication')
                    offset = row[2] * self.manifest['state_bytes']
                    key = self._maps[1][
                        offset + STATE_HEADER_PREFIX : offset + self.manifest['state_bytes']
                    ]
                    if span_key is not None and key != span_key:
                        raise ContractError('stable span changes retained liquidity or permissions')
                    span_key = key
                if (
                    row[0] < previous_time
                    or row[1] <= previous_source
                    or not version <= row[2] <= version + 1
                ):
                    raise ContractError('corrupt prepared event ordering')
                if row[2] != version:
                    self._snapshot(row, index + 1)
                previous_time, previous_source, version = row[:3]
            if version + 1 != self.manifest['states']:
                raise ContractError('prepared state count mismatch')
            self.history.validate(self.manifest['frames'], cancelled)
            self.spans.validate(self.manifest['frames'])

    def idle_span_end(self, index: int) -> int:
        """Return the index just past the stable span that contains event ``index``."""
        with self._lock:
            self._check()
            return self.spans.end(index)

    def source_sequence(self, prefix: int) -> int:
        """Original frame of a retained prefix; excluded foreign frames are not events."""
        integer(prefix, 'prepared prefix')
        with self._lock:
            self._check()
            if prefix > self.manifest['frames']:
                raise ContractError('prefix outside prepared range')
            return self._row(prefix - 1)[1] if prefix else 0

    def market_sequence(self, prefix: int) -> int:
        """Financial source ordinal; distinct from raw frame and merged news prefix."""
        integer(prefix, 'prepared prefix')
        if prefix > self.manifest['frames']:
            raise ContractError('prefix outside prepared range')
        return prefix

    def _snapshot(
        self, row: Sequence[Any], sequence: int, status: str = 'playing'
    ) -> MarketSnapshot:
        """Decode the state version an event row points to."""
        stamp, _, version = row[:3]
        size = self.manifest['state_bytes']
        raw = self._maps[1][version * size : (version + 1) * size]
        return decode_state(
            raw, self.manifest['symbol'], stamp, sequence, self.manifest['depth'], status
        )

    def prefix(self, time_ns: int) -> int:
        """Retained-event count at time_ns; include ties in original source order."""
        integer(time_ns, 'query time')
        with self._lock:
            self._check()
            low, high = 0, self.manifest['frames']
            while low < high:
                middle = (low + high) // 2
                if self._row(middle)[0] <= time_ns:
                    low = middle + 1
                else:
                    high = middle
            return low

    def event_time(self, index: int) -> int:
        integer(index, 'event index')
        with self._lock:
            self._check()
            if index >= self.manifest['frames']:
                raise ContractError('event outside prepared range')
            return self._row(index)[0]

    def snapshot_prefix(self, prefix: int) -> MarketSnapshot:
        """Return the market after the first ``prefix`` events."""
        integer(prefix, 'prepared prefix')
        with self._lock:
            self._check()
            if prefix > self.manifest['frames']:
                raise ContractError('prefix outside prepared range')
            return (
                self._snapshot(self._row(prefix - 1), prefix)
                if prefix
                else self._snapshot(_EMPTY_ROW, 0, 'ready')
            )

    def snapshot_at(self, time_ns: int) -> MarketSnapshot:
        """Return the market as of ``time_ns``, including events at exactly that time."""
        with self._lock:
            prefix = self.prefix(time_ns)
            if prefix:
                return self._snapshot(self._row(prefix - 1), prefix)
            return self._snapshot(_EMPTY_ROW, 0, 'ready')

    def event(self, index: int) -> MarketEvent:
        """Trusted execution cursor only; zero-based index is not an Agent capability."""
        integer(index, 'event index')
        with self._lock:
            self._check()
            if index >= self.manifest['frames']:
                raise ContractError('event outside prepared range')
            row = self._row(index)
            return decode_event(row, self._snapshot(row, index + 1))

    def __enter__(self) -> PreparedMarket:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        with self._lock:
            for mapping in self._maps:
                if hasattr(mapping, 'close'):
                    mapping.close()
            for stream in self._files:
                stream.close()
            self._maps.clear()
            self._files.clear()
            self._closed = True


class VisibleMarket:
    """A reader limited to what the experiment clock has reached.

    This is the only view of prepared data that Agent tools use. ``clock`` returns the
    current experiment time; nothing later than it can be read.
    """

    def __init__(self, reader: Any, clock: Callable[[], int]) -> None:
        self._reader, self._clock = reader, clock

    def capture(self, as_of_ns: Optional[int] = None) -> Dict[str, Any]:
        """Fix visibility at entry; later delivery never expands this capture."""
        cutoff = self._clock()
        integer(cutoff, 'visible time')
        target = cutoff if as_of_ns is None else as_of_ns
        integer(target, 'as-of time')
        if target > cutoff:
            raise FutureDataError('query exceeds visible market time')
        if target > self._reader.manifest['last_ns']:
            raise DataUnavailable('source coverage after the last event is not established')
        snapshot = self._reader.snapshot_at(target)
        return dict(
            target_ns=target,
            visible_ns=cutoff,
            prefix=snapshot.sequence,
            source_time_ns=snapshot.time_ns,
            source_sequence=self._reader.source_sequence(snapshot.sequence),
            snapshot=snapshot,
        )
