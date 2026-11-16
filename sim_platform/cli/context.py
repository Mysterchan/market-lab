"""One Platform for a CLI invocation; handlers borrow it without closing it."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional, Union

from sim_platform.application.platform import Platform
from sim_platform.application.lifecycle import PlatformLifecycle
from sim_platform.domain.models import ContractError
from sim_platform.persistence.paths import data_directory


class CliContext:
    """The data directory and the one Platform of a CLI invocation.

    main() creates it per invocation; handlers borrow the Platform from
    open_platform() without closing it, and close() closes it at the end.
    """

    def __init__(self, data_dir: Optional[Path] = None) -> None:
        self.data_dir = data_dir
        self._lifecycle: Optional[PlatformLifecycle] = None
        self._closed = False
        self._selected_directory: Optional[Path] = None

    def resolve_directory(self) -> Path:
        """The --data-dir override, or the OS application data directory."""
        directory = self.data_dir if self.data_dir is not None else data_directory()
        if not directory.is_absolute():
            raise ContractError('data directory must be absolute')
        return directory

    def select_directory(self, directory: Union[str, Path]) -> None:
        """Select temporary comparison storage before acquiring this invocation's Platform."""
        if self._closed or self._lifecycle is not None:
            raise ContractError('cannot change storage after platform acquisition')
        directory = Path(directory)
        if not directory.is_absolute():
            raise ContractError('data directory must be absolute')
        self._selected_directory = directory

    @contextmanager
    def open_platform(self, *, database: bool = True) -> Iterator[Platform]:
        if self._closed:
            raise ContractError('CLI context is closed')
        if self._lifecycle is None:
            directory = self._selected_directory or self.resolve_directory()
            self._lifecycle = PlatformLifecycle(directory, factory=Platform)
        yield self._lifecycle.open(database=database)

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            if self._lifecycle is not None:
                self._lifecycle.close()

    def __enter__(self) -> CliContext:
        if self._closed:
            raise ContractError('CLI context is closed')
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
