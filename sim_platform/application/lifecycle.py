"""One active Platform per application owner, including reset and recovery."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Optional, Tuple, Union

from sim_platform.domain.models import ContractError
from sim_platform.resources import cleanup_after_error
from .platform import Platform

if TYPE_CHECKING:  # Annotation only.
    from os import PathLike
    from pathlib import Path

    from sim_platform.research.controller import ResearchController


class PlatformLifecycle:
    """Owns the one open Platform of an application (desktop or CLI).

    The application creates it with its data directory; open() creates the Platform
    on first use, reset() replaces the database, and close() closes the Platform.
    """

    def __init__(
        self, directory: Union[str, PathLike[str]], *, factory: Callable[..., Platform] = Platform
    ) -> None:
        self.directory = directory
        self._factory = factory
        self._platform: Optional[Platform] = None
        self.closed = False

    def open(self, *, database: bool = True) -> Platform:
        if self.closed:
            raise ContractError('application is closed')
        if self._platform is None:
            self._platform = self._factory(self.directory, database=database)
        elif self._platform.closed:
            raise ContractError('owned platform was closed outside its lifecycle')
        return self._platform

    def reset(
        self,
        *,
        library: Optional[Union[str, PathLike[str]]] = None,
        demo_source: Optional[Tuple[Any, Any]] = None,
    ) -> ResearchController:
        """Replace the local database with a fresh one (optionally seeded with demo data)."""
        from sim_platform.persistence.workspace.reset import replace_research_storage
        from sim_platform.research.controller import seed_research

        platform = self.open()
        for profile in platform.profiles():
            if any(
                run['status'] == 'running'
                for run in platform.runs(profile['id'], include_records=False)
            ):
                raise ContractError('stop all running experiments before resetting local data')
        platform.close()
        self._platform = None

        def prepare(staging: Path) -> None:
            with self._factory(staging) as candidate:
                seed_research(candidate.open_research(library=library), demo_source)

        def reopen() -> Tuple[Platform, ResearchController]:
            candidate = self._factory(self.directory)
            try:
                controller = candidate.open_research(library=library)
                if demo_source:
                    controller.load(demo_source[0], news=demo_source[1])
                return candidate, controller
            except Exception as exc:
                cleanup_after_error(exc, candidate.close)
                raise

        try:
            self._platform, controller = replace_research_storage(
                self.directory, prepare=prepare, reopen=reopen
            )
            return controller
        except Exception as exc:
            # Storage replacement has restored the original files before this runs.
            try:
                self.open()
            except Exception as recovery:
                raise recovery from exc
            raise

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            if self._platform is not None:
                self._platform.close()

    def __enter__(self) -> PlatformLifecycle:
        if self.closed:
            raise ContractError('application is closed')
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
