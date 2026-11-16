"""Complete native reconstruction into staged immutable event/state files."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import tempfile
import time
from typing import Any, Callable, Dict, Optional

from sim_platform.domain.models import ContractError
from sim_platform.market.native import PathInput

from .bars import export_bars, is_bar_source
from .codec import state_size
from .native import NativePreparation

# What a native ITCH import retains: one session, bounded depth, no publications.
NATIVE_PROFILE = 'single-session-depth-bounded-no-external-news'

# The files that make up a prepared artifact, besides its manifest.
ARTIFACT_FILES = (
    'events.bin',
    'states.bin',
    'trades.bin',
    'bar-versions.bin',
    'bar-index.bin',
    'spans.bin',
)


class PreparationCancelled(ContractError):
    """The caller cancelled preparation before the artifact was published."""


def source_identity(path: Path) -> Dict[str, int]:
    """Describe a source file cheaply, to detect that it changed during or after a build."""
    stat = path.stat()
    return dict(
        bytes=stat.st_size, mtime_ns=stat.st_mtime_ns, device=stat.st_dev, inode=stat.st_ino
    )


def _export(
    source: Path,
    stage: Path,
    symbol: str,
    depth: int,
    library: Optional[PathInput],
    implicit: bool,
    profile: bool,
    cancelled: Callable[[], bool],
    progress: Callable[[int], Any],
) -> Dict[str, Any]:
    """Run the native exporter to the end of the source and return its final summary."""
    native = NativePreparation(source, stage, symbol, depth, library, implicit, profile)
    try:
        while True:
            if cancelled():
                raise PreparationCancelled('market preparation cancelled')
            result = native.next_batch()
            progress(result['source_frames'])
            if result['done']:
                return result
    finally:
        native.close()


def _manifest(
    source: Path,
    stage: Path,
    symbol: str,
    depth: int,
    identity: Dict[str, int],
    implicit: bool,
    result: Dict[str, Any],
) -> Dict[str, Any]:
    """Flush every artifact file to disk and describe the finished artifact.

    A bar source adds a ``calendar`` entry: the date of market day 0, the bar length
    and the assumptions behind its synthetic book. A native import has no such entry.
    """
    files = {}
    for name in ARTIFACT_FILES:
        # Windows flushes only a handle opened for writing; 'r+b' changes nothing.
        with (stage / name).open('r+b') as stream:
            os.fsync(stream.fileno())
            stat = os.fstat(stream.fileno())
            files[name] = dict(bytes=stat.st_size, mtime_ns=stat.st_mtime_ns)
    manifest = dict(
        symbol=symbol,
        depth=depth,
        frames=result['frames'],
        source_frames=result['source_frames'],
        states=result['states'],
        first_ns=result['first_ns'],
        last_ns=result['last_ns'],
        source=str(source),
        source_identity=identity,
        allow_implicit_eof=implicit,
        coverage_end_ns=None,
        profile=result.get('profile', NATIVE_PROFILE),
        state_bytes=state_size(depth),
        files=files,
        history=result['history'],
        stable_spans=result['stable_spans'],
        complete=True,
    )
    if 'calendar' in result:
        manifest['calendar'] = result['calendar']
    return manifest


def build(
    source: PathInput,
    destination: PathInput,
    *,
    symbol: str = 'AAPL',
    depth: int = 5,
    library: Optional[PathInput] = None,
    allow_implicit_eof: bool = False,
    cancelled: Callable[[], bool] = lambda: False,
    progress: Callable[[int], Any] = lambda frames: None,
    timings: Optional[Dict[str, Any]] = None,
    profile_native: bool = False,
    bar_spread: Optional[int] = None,
    bar_origin: Optional[str] = None,
    _staging_parent: Optional[PathInput] = None,
) -> Path:
    """Scan the entire source, validate once, then publish; never overwrite ready data.

    A ``.csv`` source is read as OHLCV price bars and given a synthetic book (see
    ``bars.py``) whose levels per side are ``depth`` and whose bid-ask distance is
    ``bar_spread`` (integer money; None means two cents). Any other source is decoded
    as an ITCH BinaryFILE, which has its own book and accepts no ``bar_spread``.
    ``bar_origin`` ('YYYY-MM-DD') sets a price-bar file's market day 0 to an earlier
    date, so several files share one timeline.

    Native batches retain target-symbol and system events with original frame IDs.
    Prepared ordinals are contiguous. Optional timings records import stages;
    profile_native additionally enables intrusive per-frame native instrumentation
    for diagnosis, not production throughput comparisons.
    Cancellation is checked between bounded 8192-source-frame batches and during
    initial output validation. Source must remain unchanged for the whole build.
    """
    if (
        not isinstance(symbol, str)
        or not 1 <= len(symbol) <= 8
        or not symbol.isascii()
        or symbol != symbol.strip().upper()
    ):
        raise ContractError('symbol must be 1..8 uppercase ASCII characters')
    if type(depth) is not int or not 1 <= depth <= 100:
        raise ContractError('depth must be 1..100')
    if timings is not None and type(timings) is not dict:
        raise ContractError('timings must be a dict')
    source, destination = Path(source).resolve(), Path(destination).resolve()
    from .bars import DEFAULT_SPREAD, validate_book

    if is_bar_source(source):
        validate_book(DEFAULT_SPREAD if bar_spread is None else bar_spread, depth)
    elif bar_spread is not None or bar_origin is not None:
        raise ContractError('only a price-bar recording has a spread or first date to choose')
    if destination.exists():
        raise ContractError('prepared destination already exists')
    identity = source_identity(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.prepared-', dir=_staging_parent or destination.parent))
    try:
        started = time.perf_counter()
        if is_bar_source(source):
            # A CSV of price bars: the book is synthesised, the native decoder is not used.
            result = export_bars(
                source,
                stage,
                symbol,
                depth,
                cancelled,
                progress,
                DEFAULT_SPREAD if bar_spread is None else bar_spread,
                bar_origin,
            )
        else:
            result = _export(
                source,
                stage,
                symbol,
                depth,
                library,
                allow_implicit_eof,
                profile_native,
                cancelled,
                progress,
            )
        export_seconds = time.perf_counter() - started
        started = time.perf_counter()
        from .history import build_history

        result['history'] = build_history(stage, result['frames'], depth, cancelled)
        from .spans import build_spans

        result['stable_spans'] = build_spans(stage, result['frames'], depth, cancelled)
        indexes_seconds = time.perf_counter() - started
        manifest = _manifest(source, stage, symbol, depth, identity, allow_implicit_eof, result)
        with (stage / 'manifest.json').open('w') as stream:
            json.dump(manifest, stream, separators=(',', ':'))
            stream.flush()
            os.fsync(stream.fileno())
        # Full structural validation belongs to import, not every experiment open.
        from .reader import PreparedMarket

        started = time.perf_counter()
        with PreparedMarket(stage, verify=True, cancelled=cancelled):
            pass
        validate_seconds = time.perf_counter() - started
        if cancelled():
            raise PreparationCancelled('market preparation cancelled')
        if identity != source_identity(source):
            raise ContractError('source modified during preparation')
        if destination.exists():
            raise ContractError('prepared destination already exists')
        if timings is not None:
            timings.update(
                export_seconds=export_seconds,
                validate_seconds=validate_seconds,
                history_and_spans_seconds=indexes_seconds,
                native_read_ns=result['read_ns'],
                native_apply_ns=result['apply_ns'],
                native_encode_write_ns=result['encode_write_ns'],
            )
        os.rename(stage, destination)
        return destination
    finally:
        if stage.exists():
            shutil.rmtree(stage)
