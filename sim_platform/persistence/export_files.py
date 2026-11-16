"""Atomic text file replacement; callers own content and serialization rules."""

from __future__ import annotations

import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import IO, TYPE_CHECKING, Callable, Optional, Union

if TYPE_CHECKING:  # Annotation only.
    from os import PathLike


def atomic_write(path: Union[str, PathLike[str]], serialize: Callable[[IO[str]], object]) -> str:
    """Write ``path`` via a temporary file in the same folder, then replace it in one step.

    ``serialize`` writes the content to the open text stream. Returns the path as text.
    """
    path = Path(path)
    temporary: Optional[Path] = None
    try:
        with NamedTemporaryFile(
            mode='w',
            encoding='utf-8',
            newline='',
            dir=path.parent,
            prefix='.' + path.name,
            suffix='.tmp',
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            serialize(stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return str(path)
