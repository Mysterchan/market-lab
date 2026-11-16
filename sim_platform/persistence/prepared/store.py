"""Local immutable artifact inventory, with one cross-process importer per store.

A run opens its own read-only mapping; closing one mapping never invalidates
another. This store deliberately has no artifact deletion/automatic eviction.
"""

from __future__ import annotations

from pathlib import Path
from threading import RLock
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Union, cast
from uuid import uuid4
import json
from sim_platform.persistence.locking import acquire_exclusive, release_exclusive
from sim_platform.domain.models import ContractError
from sim_platform.market.prepared.builder import source_identity
from sim_platform.market.prepared.reader import PreparedMarket
from sim_platform.market.prepared.job import PreparationJob
from sim_platform.persistence.datasets.files import _atomic_json

if TYPE_CHECKING:  # Annotation only.
    from os import PathLike


# Default preparation options.
DEFAULT_SYMBOL = 'AAPL'
DEFAULT_DEPTH = 5

# One inventory entry: 'id' (artifact folder), 'source', 'identity' and 'options'.
InventoryEntry = Dict[str, Any]


class ImportLease:
    """The store's exclusive import lock, held by one PreparedImport until it ends."""

    def __init__(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        self._stream = (directory / 'import.lock').open('ab')
        try:
            acquired = acquire_exclusive(self._stream, blocking=False)
        except Exception:
            self._stream.close()
            raise
        if not acquired:
            self._stream.close()
            raise ContractError('another prepared import is active in this workspace') from None

    def close(self) -> None:
        if not self._stream.closed:
            release_exclusive(self._stream)
            self._stream.close()


class PreparedImport:
    """Own a preparation job and inventory lease until publication or cancellation."""

    def __init__(
        self,
        store: PreparedStore,
        lease: ImportLease,
        source: Path,
        identity: Any,
        options: Dict[str, Any],
        destination: Path,
    ) -> None:
        self._store, self._lease = store, lease
        self._source, self._identity = source, identity
        self._options, self.destination = options, destination
        self._published = False
        self._job: Optional[PreparationJob] = None
        try:
            self._job = PreparationJob(source, destination, **options)
        except Exception:
            lease.close()
            raise

    def snapshot(self) -> Dict[str, Any]:
        # The constructor either sets the job or raises.
        return cast(PreparationJob, self._job).snapshot()

    def result(self, timeout: float = 0) -> Path:
        """Wait up to ``timeout`` for the job, register the artifact and return its path."""
        if self._published:
            return self.destination
        path = cast(PreparationJob, self._job).result(timeout)
        # Job publishes only fully validated output; inventory publication follows.
        with PreparedMarket(path):
            pass
        if source_identity(self._source) != self._identity:
            raise ContractError('source changed after preparation; inventory not registered')
        self._store.register(path, self._source, self._identity, self._options)
        self._published = True
        self.close()
        return path

    def cancel(self) -> None:
        cast(PreparationJob, self._job).cancel()

    def close(self) -> None:
        try:
            if self._job is not None:
                self._job.close()
        finally:
            # Retain the lease if the child still owns output files.
            if self._job is None or not self._job.snapshot()['alive']:
                self._lease.close()


class PreparedStore:
    """The inventory of prepared market artifacts in a data folder.

    Platform creates one per data folder. Artifacts are never deleted; begin()
    returns an existing artifact or an import that registers a new one.
    """

    def __init__(self, directory: Union[str, PathLike[str]]) -> None:
        self.directory = Path(directory) / 'prepared-market'
        self._registry = self.directory / 'inventory.json'
        self._lock = RLock()

    def _entries(self) -> List[InventoryEntry]:
        if not self._registry.exists():
            return []
        value = json.loads(self._registry.read_text())
        if set(value) != {'entries'} or type(value.get('entries')) is not list:
            raise ContractError('unsupported prepared inventory')
        return value['entries']

    def _match(self, source: Path, identity: Any, options: Dict[str, Any]) -> Optional[Path]:
        for entry in self._entries():
            if (
                entry['source'] == str(source)
                and entry['identity'] == identity
                and entry['options'] == options
            ):
                path = self.directory / entry['id']
                with PreparedMarket(path):
                    pass
                return path
        return None

    def begin(
        self,
        source: Union[str, PathLike[str]],
        *,
        symbol: str = DEFAULT_SYMBOL,
        depth: int = DEFAULT_DEPTH,
        allow_implicit_eof: bool = False,
        library: Optional[Union[str, PathLike[str]]] = None,
        bar_spread: Optional[int] = None,
        bar_origin: Optional[str] = None,
    ) -> Union[Path, PreparedImport]:
        """Return a reusable path or an owned asynchronous import; never hash the input.

        ``bar_spread`` chooses a price-bar recording's synthetic spread. It is part of the
        artifact's identity only when given, so earlier inventory entries still match.
        """
        source = Path(source).expanduser().resolve()
        identity = source_identity(source)
        options: Dict[str, Any] = dict(
            symbol=symbol, depth=depth, allow_implicit_eof=allow_implicit_eof
        )
        if bar_spread is not None:
            options['bar_spread'] = bar_spread
        if bar_origin is not None:
            options['bar_origin'] = bar_origin
        # Native library is an implementation input; keep an explicit stat identity.
        from sim_platform.market.native import resolve_library_path

        native = resolve_library_path(library)
        inventory_options = dict(
            options, native_path=str(native), native_identity=source_identity(native)
        )
        with self._lock:
            existing = self._match(source, identity, inventory_options)
            if existing is not None:
                return existing
            lease = ImportLease(self.directory)
            try:
                # Another importer may have published between the first read and lease.
                existing = self._match(source, identity, inventory_options)
                if existing is not None:
                    lease.close()
                    return existing
                return PreparedImport(
                    self,
                    lease,
                    source,
                    identity,
                    dict(options, library=str(native)),
                    self.directory / uuid4().hex,
                )
            except Exception:
                lease.close()
                raise

    def register(self, path: Path, source: Path, identity: Any, options: Dict[str, Any]) -> None:
        """Caller holds the import lease; no concurrent registry read/modify/write."""
        from sim_platform.market.native import resolve_library_path

        native = resolve_library_path(options.get('library'))
        recorded = {
            k: options[k]
            for k in ('symbol', 'depth', 'allow_implicit_eof', 'bar_spread', 'bar_origin')
            if k in options
        }
        recorded.update(native_path=str(native), native_identity=source_identity(native))
        entry = dict(id=path.name, source=str(source), identity=identity, options=recorded)
        with self._lock:
            entries = self._entries()
            entries.append(entry)
            _atomic_json(self._registry, dict(entries=entries))
