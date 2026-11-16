"""Non-SQL scratch-file IO. Paths are allocated by the app workspace."""

from __future__ import annotations

from pathlib import Path
import re
import shutil

from sim_platform.domain.models import ContractError

# Allowed cache file names.
NAME_PATTERN = r'[a-z0-9_-]+'


class MarketCacheFiles:
    """Named scratch files in one playback folder of the App workspace.

    The workspace allocates the folder; the playback that uses it owns this object,
    and close() deletes the folder.
    """

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self._closed = False

    def _path(self, name: str) -> Path:
        if self._closed:
            raise ContractError('market cache is closed')
        if not re.fullmatch(NAME_PATTERN, name):
            raise ContractError('invalid cache file name')
        return self.directory / name

    def size(self, name: str) -> int:
        path = self._path(name)
        return path.stat().st_size if path.exists() else 0

    def append(self, name: str, value: bytes) -> int:
        """Append ``value`` and return the offset it was written at."""
        with self._path(name).open('ab') as stream:
            offset = stream.tell()
            stream.write(value)
            return offset

    def read(self, name: str, offset: int, size: int) -> bytes:
        with self._path(name).open('rb') as stream:
            stream.seek(offset)
            value = stream.read(size)
        if len(value) != size:
            raise ContractError('truncated market cache')
        return value

    def write(self, name: str, offset: int, value: bytes) -> None:
        with self._path(name).open('r+b') as stream:
            stream.seek(offset)
            stream.write(value)

    def remove(self, name: str) -> None:
        self._path(name).unlink(missing_ok=True)

    def close(self) -> None:
        if not self._closed:
            if self.directory.exists():
                shutil.rmtree(self.directory)
            self._closed = True
