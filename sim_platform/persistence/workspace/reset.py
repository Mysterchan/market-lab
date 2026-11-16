"""Research database replacement and recovery; lifecycle callbacks supplied by the application owner."""

from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Any, Callable, Optional, Sequence, TypeVar, Union, cast

if TYPE_CHECKING:  # Annotation only.
    from os import PathLike


# What reopen() returns; its first item is closed if replacement fails afterwards.
T = TypeVar('T', bound=Sequence[Any])


def replace_research_storage(
    directory: Union[str, PathLike[str]],
    *,
    prepare: Callable[[Path], None],
    reopen: Callable[[], T],
) -> T:
    """Caller must close its session first. Never move or delete dataset folders."""
    directory = Path(directory)
    database = directory / 'platform.sqlite'
    settings = directory / 'desktop-settings.json'
    # Prepare the fresh database before changing current storage. Old files are
    # held only during replacement so a failed operation can be rolled back.
    with TemporaryDirectory(prefix='.reset-', dir=directory) as temporary:
        staging = Path(temporary)
        prepare(staging)
        old_database = staging / 'previous.sqlite'
        old_settings = staging / 'previous-settings.json'
        opened: Optional[T] = None
        try:
            os.replace(database, old_database)
            if settings.exists():
                os.replace(settings, old_settings)
            os.replace(staging / 'platform.sqlite', database)
            opened = reopen()
        except Exception:
            if opened:
                opened[0].close()
            if old_database.exists():
                os.replace(old_database, database)
            if old_settings.exists():
                os.replace(old_settings, settings)
            raise
    # reopen() succeeded, so opened is set.
    return cast(T, opened)
