"""Dataset file operations; no Qt, playback or database connection."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Union

if TYPE_CHECKING:  # Annotation only.
    from os import PathLike, stat_result

    from sim_platform.research.jobs import Cancellation

# Bytes read per step while hashing or copying a file (1 MiB).
CHUNK_BYTES = 1024 * 1024

PathInput = Union[str, 'PathLike[str]']
# Receives {'phase', 'completed', 'total'} while a file is hashed or copied.
ProgressCallback = Callable[[Dict[str, Any]], None]


def _atomic_json(path: Path, value: Any) -> None:
    """Write ``value`` as indented JSON through a temporary file, then replace ``path``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, suffix='.tmp')
    try:
        with os.fdopen(descriptor, 'w') as output:
            json.dump(value, output, indent=2)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _digest(
    path: Path,
    cancellation: Optional[Cancellation] = None,
    progress: Optional[ProgressCallback] = None,
) -> str:
    digest = hashlib.sha256()
    total, completed = path.stat().st_size, 0
    with path.open('rb') as source:
        for block in iter(lambda: source.read(CHUNK_BYTES), b''):
            _check_cancelled(cancellation)
            digest.update(block)
            completed += len(block)
            if progress:
                progress(dict(phase='hashing', completed=completed, total=total))
    _check_cancelled(cancellation)
    return digest.hexdigest()


def _check_cancelled(cancellation: Optional[Cancellation]) -> None:
    if cancellation is not None and cancellation.cancelled:
        raise ValueError('Dataset operation cancelled')


class DatasetFiles:
    """File operations for the dataset library and desktop preferences in a data folder.

    Platform creates one per data directory and injects it into DatasetLibrary and
    research controllers. It holds only paths; every call opens and closes its files.
    """

    def __init__(self, directory: PathInput) -> None:
        self.directory = Path(directory)
        self.settings = self.directory / 'desktop-settings.json'
        self.registry = self.directory / 'dataset-library.json'

    @staticmethod
    def stat(path: PathInput) -> stat_result:
        return Path(path).stat()

    @staticmethod
    def exists(path: PathInput) -> bool:
        return Path(path).exists()

    @staticmethod
    def is_file(path: PathInput) -> bool:
        return Path(path).is_file()

    @staticmethod
    def is_dir(path: PathInput) -> bool:
        return Path(path).is_dir()

    @staticmethod
    def list_directory(path: PathInput) -> List[Path]:
        return sorted(Path(path).iterdir(), key=lambda p: p.name.lower())

    @staticmethod
    def identity(path: PathInput) -> Dict[str, Any]:
        """Resolved 'path' with its 'bytes' and 'mtime_ns'."""
        path = Path(path).expanduser().resolve()
        stat = path.stat()
        return dict(path=str(path), bytes=stat.st_size, mtime_ns=stat.st_mtime_ns)

    def preferences(self) -> Dict[str, Any]:
        return json.loads(self.settings.read_text()) if self.settings.exists() else {}

    def entries(self) -> List[Dict[str, Any]]:
        return json.loads(self.registry.read_text())['datasets'] if self.registry.exists() else []

    def save(self, entries: List[Dict[str, Any]]) -> None:
        _atomic_json(self.registry, {'version': 1, 'datasets': entries})

    def set_preferences(self, **changes: Any) -> None:
        preferences = self.preferences()
        preferences.update(changes)
        _atomic_json(self.settings, preferences)

    @staticmethod
    def digest(
        path: Path,
        cancellation: Optional[Cancellation] = None,
        progress: Optional[ProgressCallback] = None,
    ) -> str:
        return _digest(path, cancellation, progress)

    def copy(
        self,
        source: Path,
        root: Path,
        identity: str,
        checksum: str,
        cancellation: Optional[Cancellation] = None,
        progress: Optional[ProgressCallback] = None,
    ) -> Path:
        """Copy ``source`` into ``root`` as ``identity``; raise if its checksum changed."""
        root.mkdir(parents=True, exist_ok=True)
        destination = root / (identity + source.suffix.lower())
        descriptor, temporary = tempfile.mkstemp(dir=root, suffix='.importing')
        os.close(descriptor)
        try:
            digest, completed, total = hashlib.sha256(), 0, source.stat().st_size
            with source.open('rb') as incoming, open(temporary, 'wb') as outgoing:
                for block in iter(lambda: incoming.read(CHUNK_BYTES), b''):
                    _check_cancelled(cancellation)
                    outgoing.write(block)
                    digest.update(block)
                    completed += len(block)
                    if progress:
                        progress(dict(phase='copying', completed=completed, total=total))
                outgoing.flush()
                os.fsync(outgoing.fileno())
            _check_cancelled(cancellation)
            if digest.hexdigest() != checksum:
                raise ValueError('Dataset changed while being copied; import was cancelled')
            os.replace(temporary, destination)
            return destination
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    @staticmethod
    def remove(path: Path) -> None:
        path.unlink(missing_ok=True)
