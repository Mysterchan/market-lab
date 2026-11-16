"""Read-only file identity checks used by application replay manifests."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING, Iterable, List, Union

from sim_platform.persistence.datasets.files import DatasetFiles

if TYPE_CHECKING:  # Annotation only.
    from os import PathLike


def digest(path: Union[str, PathLike[str]]) -> str:
    """SHA-256 of a file's contents, as hexadecimal text."""
    return DatasetFiles.digest(Path(path))


def implementation_digest(root: Path, paths: Iterable[Path]) -> str:
    """SHA-256 over each file's path relative to ``root`` and its bytes, in order."""
    value = sha256()
    for path in paths:
        value.update(str(path.relative_to(root)).encode())
        value.update(path.read_bytes())
    return value.hexdigest()


def python_files(directory: Path) -> List[Path]:
    return sorted(directory.glob('*.py'))
