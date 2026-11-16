"""Dataset identity and import workflow, using injected file operations."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Tuple
from uuid import uuid4
from sim_platform.domain.models import ContractError

if TYPE_CHECKING:  # Annotation only.
    from sim_platform.market.native import PathInput
    from .jobs import Cancellation

# Market data file extensions the library accepts (compared in lower case).
# ITCH BinaryFILE recordings, and CSV files of OHLCV price bars.
DATASET_SUFFIXES = ('.bin', '.itch', '.csv')

# One saved dataset: 'id', 'name', 'path', 'original_path', 'managed', 'bytes',
# 'mtime_ns', 'sha256' and, for a price-bar file added with chosen options, 'book'
# ({'spread', 'depth'}; see market.prepared.bars.book_options). catalog() adds 'status'
# ('Available', 'Changed' or 'Missing').
DatasetEntry = Dict[str, Any]
# The library listing: 'folder', 'storage_root' and 'files' (DatasetEntry with status).
Catalog = Dict[str, Any]
# Receives progress dictionaries while a file is hashed or copied.
ProgressCallback = Callable[[Dict[str, Any]], None]


class DatasetLibrary:
    """The saved list of market data files and the folder that managed copies go to.

    Platform creates one per library request with its DatasetFiles adapter and
    registers it as a resource; close() marks it closed and runs the on_close
    callback. Every change is saved through the adapter immediately.

    Args:
        files: DatasetFiles adapter (persistence package, not yet typed).
        demo_source: Optional (market path, news path) pair to add on creation.
    """

    def __init__(
        self, files: Any, demo_source: Optional[Tuple[PathInput, Optional[PathInput]]] = None
    ) -> None:
        self._files = files
        self._closed = False
        self._on_close: Callable[[], None] = lambda: None
        self.directory, self.registry = files.directory, files.registry
        preferences = files.preferences()
        self.folder = preferences.get('dataset_folder', '')
        self.storage_root = preferences.get(
            'dataset_storage', str((self.directory / 'datasets').resolve())
        )
        self.entries = files.entries()
        # Migrate the former folder-only inventory once; future refreshes use saved entries.
        if (
            not self._files.exists(self.registry)
            and self.folder
            and self._files.is_dir(self.folder)
        ):
            self.catalog(folder=self.folder)
        if demo_source:
            self.add(demo_source[0], copy=False)

    def on_close(self, callback: Callable[[], None]) -> None:
        self._on_close = callback

    def close(self) -> None:
        self._closed = True
        self._on_close()

    def _check_open(self) -> None:
        if self._closed:
            raise ContractError('dataset library is closed')

    def _save(self, entries: List[DatasetEntry]) -> None:
        self._files.save(entries)
        self.entries = entries

    def _preferences(self, **changes: Any) -> None:
        self._files.set_preferences(**changes)

    def set_storage(self, folder: PathInput) -> Catalog:
        self._check_open()
        path = Path(folder).expanduser().resolve()
        if not self._files.is_dir(path):
            raise ValueError('Storage folder does not exist or is unavailable')
        self._preferences(dataset_storage=str(path))
        self.storage_root = str(path)
        return self.catalog()

    def add(
        self,
        path: PathInput,
        copy: bool = False,
        *,
        cancellation: Optional[Cancellation] = None,
        progress: Optional[ProgressCallback] = None,
        book: Optional[Dict[str, int]] = None,
    ) -> Catalog:
        """Register a market file (optionally copying it into storage) unless already listed.

        ``book`` saves a price-bar file's synthetic spread and depth with its entry; the
        Desktop prepares that recording with them. Adding a listed file again with a
        different ``book`` replaces the saved choice.
        """
        self._check_open()
        source = Path(path).expanduser().resolve()
        if not self._files.is_file(source) or source.suffix.lower() not in DATASET_SUFFIXES:
            raise ValueError(
                'Choose an existing .bin or .itch recording, or a .csv file of price bars'
            )
        from sim_platform.market.prepared.bars import book_options, is_bar_source

        book = book_options(book)
        if book is not None and not is_bar_source(source):
            raise ValueError('Spread and depth can be chosen only for a .csv price-bar file')
        source_stat = self._files.stat(source)
        options = (
            dict(cancellation=cancellation, progress=progress) if cancellation is not None else {}
        )
        checksum = self._files.digest(source, **options)
        if (self._files.stat(source).st_size, self._files.stat(source).st_mtime_ns) != (
            source_stat.st_size,
            source_stat.st_mtime_ns,
        ):
            raise ValueError(
                'Dataset changed while being added; try again when writing has finished'
            )
        existing = next(
            (
                entry
                for entry in self.entries
                if entry['sha256'] == checksum
                and entry['managed'] == bool(copy)
                and self._files.is_file(entry['path'])
                and self._files.stat(entry['path']).st_size == entry['bytes']
                and self._files.stat(entry['path']).st_mtime_ns == entry['mtime_ns']
                and (copy or entry['path'] == str(source))
            ),
            None,
        )
        if existing:
            if book is not None and existing.get('book') != book:
                self._save([dict(e, book=book) if e is existing else e for e in self.entries])
            return self.catalog()
        identity = uuid4().hex
        destination = source
        copied = False
        try:
            if copy:
                root = Path(self.storage_root)
                destination = self._files.copy(source, root, identity, checksum, **options)
                copied = True
            stat = self._files.stat(destination)
            entry = dict(
                id=identity,
                name=source.name,
                path=str(destination),
                original_path=str(source),
                managed=bool(copy),
                bytes=stat.st_size,
                mtime_ns=stat.st_mtime_ns,
                sha256=checksum,
            )
            if book is not None:
                entry['book'] = book
            if cancellation is not None and cancellation.cancelled:
                raise ValueError('Dataset operation cancelled')
            self._save(self.entries + [entry])
        except Exception:
            if copied:
                self._files.remove(destination)
            raise
        return self.catalog()

    def locate(
        self,
        identity: str,
        path: PathInput,
        *,
        cancellation: Optional[Cancellation] = None,
        progress: Optional[ProgressCallback] = None,
    ) -> Catalog:
        """Point a listed dataset at a moved file whose checksum is unchanged."""
        self._check_open()
        entry = next((entry for entry in self.entries if entry['id'] == identity), None)
        if entry is None:
            raise ValueError('Dataset is no longer in the library')
        source = Path(path).expanduser().resolve()
        options = (
            dict(cancellation=cancellation, progress=progress) if cancellation is not None else {}
        )
        if (
            not self._files.is_file(source)
            or self._files.digest(source, **options) != entry['sha256']
        ):
            raise ValueError('This is not the same dataset. Add changed data as a new dataset.')
        stat = self._files.stat(source)
        replacement = dict(
            entry, path=str(source), managed=False, bytes=stat.st_size, mtime_ns=stat.st_mtime_ns
        )
        self._save([replacement if item['id'] == identity else item for item in self.entries])
        return self.catalog()

    def catalog(self, folder: Optional[PathInput] = None) -> Catalog:
        self._check_open()
        # Legacy API: explicitly choosing a folder registers its files, without moving them.
        if folder is not None:
            directory = Path(folder).expanduser().resolve()
            if not self._files.is_dir(directory):
                raise ValueError('Dataset folder does not exist or is unavailable')
            for path in self._files.list_directory(directory):
                if self._files.is_file(path) and path.suffix.lower() in DATASET_SUFFIXES:
                    self.add(path)
            self._preferences(dataset_folder=str(directory))
            self.folder = str(directory)
        files = []
        for entry in self.entries:
            path = Path(entry['path'])
            try:
                stat = self._files.stat(path)
                status = (
                    'Available'
                    if self._files.is_file(path)
                    and stat.st_size == entry['bytes']
                    and stat.st_mtime_ns == entry['mtime_ns']
                    else 'Changed'
                )
            except OSError:
                status = 'Missing'
            files.append(dict(entry, status=status))
        return dict(folder=self.folder, storage_root=self.storage_root, files=files)
