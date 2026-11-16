"""App-owned temporary files. Locks distinguish stale workspaces from live apps."""

from __future__ import annotations

from pathlib import Path
import shutil
from tempfile import gettempdir, mkdtemp
from typing import TYPE_CHECKING, Dict, Optional, Union

from sim_platform.persistence.locking import acquire_exclusive
from sim_platform.domain.models import ContractError
from sim_platform.persistence.workspace.market_cache import MarketCacheFiles

if TYPE_CHECKING:  # Annotation only.
    from os import PathLike

    from .agent_source import AgentSourceFiles


class AppWorkspace:
    """This App instance's temporary folder, locked so other instances leave it alone.

    Platform creates one at start; it removes stale workspaces of ended apps, and
    close() deletes its own folder. Agent sources and market caches live inside it.
    """

    def __init__(self, root: Optional[Union[str, PathLike[str]]] = None) -> None:
        root = Path(root) if root is not None else Path(gettempdir()) / 'sim-platform-cache'
        root.mkdir(parents=True, exist_ok=True)
        self._root = root
        # Serialize cleanup and publication of a new owner's lock. Otherwise a
        # second app could remove a just-created workspace before it is locked.
        with (root / 'maintenance.lock').open('ab') as maintenance:
            acquire_exclusive(maintenance)
            self._clean_stale(root)
            self.path = Path(mkdtemp(prefix='workspace-', dir=root))
            try:
                self._lock = (self.path / 'owner.lock').open('wb')
                acquire_exclusive(self._lock)
            except Exception:
                if hasattr(self, '_lock'):
                    self._lock.close()
                shutil.rmtree(self.path)
                raise
        self._closed = False

    @staticmethod
    def _clean_stale(root: Path) -> None:
        for path in root.glob('workspace-*'):
            lock = path / 'owner.lock'
            # Creation holds maintenance.lock too, so an ownerless directory
            # encountered here belongs to an interrupted initializer.
            if not lock.is_file():
                shutil.rmtree(path)
                continue
            try:
                with lock.open('a+b') as owner:
                    if not acquire_exclusive(owner, blocking=False):
                        continue
                # Windows cannot remove owner.lock while this handle is open.
                # The caller still holds maintenance.lock, excluding creation/close.
                shutil.rmtree(path)
            except FileNotFoundError:
                continue  # Another app already cleaned it.

    def agent_source(self, files: Dict[str, str]) -> AgentSourceFiles:
        """Allocate a writable execution copy; the host closes it before App workspace cleanup."""
        if self._closed:
            raise ContractError('workspace is closed')
        from .agent_source import AgentSourceFiles

        return AgentSourceFiles(mkdtemp(prefix='agent-', dir=self.path), files)

    def market_cache(self) -> MarketCacheFiles:
        if self._closed:
            raise ContractError('workspace is closed')
        return MarketCacheFiles(Path(mkdtemp(prefix='playback-', dir=self.path)))

    def close(self) -> None:
        if not self._closed:
            with (self._root / 'maintenance.lock').open('ab') as maintenance:
                acquire_exclusive(maintenance)
                # Keep cleanup serialized while releasing the Windows file handle.
                self._lock.close()
                try:
                    shutil.rmtree(self.path)
                except FileNotFoundError:
                    pass  # A later initializer may have cleaned a failed prior close.
                self._closed = True
