"""Write-once lineage files and cached content digests of research inputs.

Each run's lineage lives in ``<data>/lineage/<run_id>/`` as one JSON file per part
(``inputs``, ``records``, ``agent``). A part is created once and never replaced, so a
later write cannot quietly change what was recorded. Digests of large inputs are cached
by path, size, modification time and inode, in memory and in ``lineage/digests.json``,
so a recording is hashed once, not once per process.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
from threading import RLock
from typing import Any, Dict, Optional, Tuple, Union

from sim_platform.domain.models import ContractError

# Bytes read per block while hashing.
CHUNK_BYTES = 1 << 20
# Parts a run's lineage may hold.
PARTS = ('inputs', 'records', 'agent')
# Most files hashed in one input folder; a larger folder is recorded as too large.
MAX_FOLDER_FILES = 10_000
_RUN_ID = re.compile(r'[A-Za-z0-9_.:-]{1,128}')


def file_digest(path: Path) -> str:
    """SHA-256 of a file's bytes, as hex."""
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(CHUNK_BYTES), b''):
            digest.update(block)
    return digest.hexdigest()


def _stat_key(path: Path) -> Tuple[str, int, int, int]:
    stat = path.stat()
    return (str(path), stat.st_size, stat.st_mtime_ns, stat.st_ino)


class LineageFiles:
    """Lineage parts under a data folder; created by Platform, thread-safe."""

    def __init__(self, directory: Union[str, os.PathLike]) -> None:
        self.root = Path(directory) / 'lineage'
        self._digests: Optional[Dict[str, str]] = None
        self._lock = RLock()

    def _cache(self) -> Dict[str, str]:
        """Digests by file identity, loaded from disk on first use."""
        if self._digests is None:
            try:
                loaded = json.loads((self.root / 'digests.json').read_text(encoding='utf-8'))
                self._digests = loaded if isinstance(loaded, dict) else {}
            except (OSError, ValueError):
                self._digests = {}
        return self._digests

    def _remember(self, key: str, digest: str) -> None:
        cache = self._cache()
        cache[key] = digest
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = self.root / f'digests.{os.getpid()}.tmp'
        temporary.write_text(json.dumps(cache, sort_keys=True), encoding='utf-8')
        os.replace(temporary, self.root / 'digests.json')

    def _folder(self, run_id: str) -> Path:
        if not isinstance(run_id, str) or _RUN_ID.fullmatch(run_id) is None:
            raise ContractError('invalid run ID')
        return self.root / run_id.replace(':', '_')

    def digest(self, path: Path, *, fresh: bool = False) -> Dict[str, Any]:
        """Describe an input file or folder by size and SHA-256, using the cache.

        A folder's digest covers each file's relative path and digest in sorted order.
        ``fresh`` reads every byte again instead of trusting the cache, as verification
        must: a file edited with its size and modification time restored is still caught.
        Returns ``{'kind', 'bytes', 'sha256'}`` and, for a folder, ``'files'``.
        """
        if path.is_dir():
            files = sorted(p for p in path.rglob('*') if p.is_file())
            if len(files) > MAX_FOLDER_FILES:
                raise ContractError('input folder has too many files to hash')
            digest = hashlib.sha256()
            size = 0
            for item in files:
                entry = self.digest(item, fresh=fresh)
                size += entry['bytes']
                digest.update(f'{item.relative_to(path).as_posix()}\0{entry["sha256"]}\n'.encode())
            return dict(kind='folder', bytes=size, files=len(files), sha256=digest.hexdigest())
        identity = _stat_key(path)
        key = '|'.join(str(part) for part in identity)
        with self._lock:
            cached = None if fresh else self._cache().get(key)
        if cached is None:
            cached = file_digest(path)
            with self._lock:
                self._remember(key, cached)
        return dict(kind='file', bytes=identity[1], sha256=cached)

    def write_once(self, run_id: str, part: str, value: Dict[str, Any]) -> bool:
        """Create a run's lineage part; return False if it already exists (kept as is)."""
        if part not in PARTS:
            raise ContractError('unknown lineage part')
        folder = self._folder(run_id)
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f'{part}.json'
        data = json.dumps(value, sort_keys=True, indent=1).encode()
        try:
            descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
        except FileExistsError:
            return False
        with os.fdopen(descriptor, 'wb') as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        return True

    def read(self, run_id: str) -> Dict[str, Optional[Dict[str, Any]]]:
        """Every recorded part of a run's lineage; a missing part is None."""
        folder = self._folder(run_id)
        parts: Dict[str, Optional[Dict[str, Any]]] = {}
        for part in PARTS:
            path = folder / f'{part}.json'
            try:
                parts[part] = json.loads(path.read_text(encoding='utf-8'))
            except FileNotFoundError:
                parts[part] = None
        return parts
