"""Atomic readiness records and a cancellable cross-process dependency-preparation lease."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
from typing import TYPE_CHECKING, Any, Callable, ContextManager, Dict, List, Optional, Union
from uuid import uuid4
from sim_platform.persistence.export_files import atomic_write
from .lease import preparation_lease

if TYPE_CHECKING:  # Annotation only.
    from os import PathLike


# A ready environment record: 'environment_ref', 'specification', 'packages', 'state'.
EnvironmentRecord = Dict[str, Any]


class EnvironmentFiles:
    """Prepared Python environments for Agents under the data folder.

    Created by the Agent runtime manager; it holds paths only. ``python_path`` maps an
    environment folder to its interpreter for the current OS.
    """

    def __init__(
        self, directory: Union[str, PathLike[str]], *, python_path: Callable[[Path], Path]
    ) -> None:
        self.root = Path(directory) / 'agents' / 'environments'
        self._python_path = python_path

    def lease(self, cancelled: Callable[[], bool]) -> ContextManager[bool]:
        return preparation_lease(self.root, cancelled)

    def find(self, specification: Any) -> Optional[EnvironmentRecord]:
        """A ready environment built for ``specification``, if any."""
        if not self.root.exists():
            return None
        for path in sorted(self.root.glob('*/environment.json')):
            try:
                value = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            if (
                type(value) is dict
                and value.get('state') == 'ready'
                and value.get('specification') == specification
                and value.get('environment_ref') == path.parent.name
                and self._python_path(path.parent / 'python').is_file()
                and (path.parent / 'python' / 'pyvenv.cfg').is_file()
            ):
                return value
        return None

    def executable(self, environment: EnvironmentRecord) -> Path:
        return self._python_path(self.root / environment['environment_ref'] / 'python')

    def allocate(self) -> Path:
        reference = uuid4().hex
        target = self.root / reference
        target.mkdir(parents=True)
        return target

    @staticmethod
    def publish(target: Path, specification: Any, packages: List[Any]) -> EnvironmentRecord:
        """Mark ``target`` ready by writing its environment.json record."""
        value = dict(
            environment_ref=target.name,
            specification=specification,
            packages=packages,
            state='ready',
        )
        atomic_write(target / 'environment.json', lambda stream: json.dump(value, stream))
        return value

    @staticmethod
    def discard(target: Path) -> None:
        shutil.rmtree(target)
