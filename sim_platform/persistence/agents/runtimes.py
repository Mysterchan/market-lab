"""Managed runtime directories, records, locks and safe offline archive extraction."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path, PurePosixPath
import shutil
import tarfile
from typing import TYPE_CHECKING, IO, Any, Callable, ContextManager, Dict, List, Tuple, Union, cast
from uuid import uuid4
from sim_platform.domain.models import ContractError
from sim_platform.persistence.export_files import atomic_write
from .lease import preparation_lease

if TYPE_CHECKING:  # Annotation only.
    from os import PathLike


# Archive bounds: most entries, and most total uncompressed bytes (2 GiB).
MAX_ARCHIVE_MEMBERS = 100000
MAX_ARCHIVE_BYTES = 2 * 1024**3
# Accepted Python version text, for example 3.12.4.
VERSION_PATTERN = r'3\.[0-9]{1,2}\.[0-9]{1,3}'
# Extracted files keep at most these permission bits.
EXTRACTED_MODE_MASK = 0o755

# A ready runtime record: 'runtime_ref', 'version', 'executable', 'origin', 'state', ...
RuntimeRecord = Dict[str, Any]


class RuntimeFiles:
    """Managed Python runtimes for Agents under the data folder.

    Created by the Agent runtime manager; it holds paths only.
    """

    def __init__(self, directory: Union[str, PathLike[str]]) -> None:
        self.root = Path(directory) / 'agents' / 'runtimes'

    def lease(self, cancelled: Callable[[], bool]) -> ContextManager[bool]:
        return preparation_lease(self.root, cancelled)

    def allocate(self) -> Path:
        target = self.root / uuid4().hex
        target.mkdir(parents=True)
        return target

    def records(self) -> List[RuntimeRecord]:
        """Every ready runtime record whose executable still exists."""
        result = []
        for path in self.root.glob('*/runtime.json'):
            try:
                value = json.loads(path.read_text())
                if (
                    type(value) is dict
                    and value.get('runtime_ref') == path.parent.name
                    and isinstance(value.get('version'), str)
                    and re.fullmatch(VERSION_PATTERN, value['version'])
                    and value.get('state') == 'ready'
                    and Path(value.get('executable', '')).is_file()
                ):
                    result.append(value)
            except (ValueError, OSError, TypeError):
                continue
        return result

    @staticmethod
    def publish(target: Path, details: Dict[str, Any], origin: Any) -> RuntimeRecord:
        record = dict(details, runtime_ref=target.name, origin=origin, state='ready')
        atomic_write(target / 'runtime.json', lambda stream: json.dump(record, stream))
        return record

    @staticmethod
    def discard(target: Path) -> None:
        shutil.rmtree(target)

    @staticmethod
    def unpack(
        archive: Union[str, PathLike[str]], target: Path, cancelled: Callable[[], object]
    ) -> None:
        """Only extract normal files and links confined to a fresh private candidate."""
        with tarfile.open(archive, 'r:gz') as bundle:
            members = bundle.getmembers()
            if (
                len(members) > MAX_ARCHIVE_MEMBERS
                or sum(item.size for item in members) > MAX_ARCHIVE_BYTES
            ):
                raise ContractError('Python archive exceeds supported capacity')
            names = set()
            links: List[Tuple[tarfile.TarInfo, Path]] = []
            for item in members:
                cancelled()
                path = PurePosixPath(item.name)
                if (
                    path.is_absolute()
                    or '..' in path.parts
                    or '\\' in item.name
                    or item.name in names
                    or not (item.isfile() or item.isdir() or item.issym() or item.islnk())
                ):
                    raise ContractError('unsafe or duplicate Python archive entry')
                names.add(item.name)
                destination = target.joinpath(*path.parts)
                if item.isdir():
                    destination.mkdir(parents=True, exist_ok=True)
                elif item.isfile():
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    # extractfile returns a stream for every regular file member.
                    member = cast(IO[bytes], bundle.extractfile(item))
                    with member as stream, destination.open('xb') as output:
                        shutil.copyfileobj(stream, output)
                    destination.chmod(item.mode & EXTRACTED_MODE_MASK)
                else:
                    links.append((item, destination))
            for item, destination in links:
                cancelled()
                link = PurePosixPath(item.linkname)
                if link.is_absolute() or '\\' in item.linkname:
                    raise ContractError('Python archive contains an external link')
                source = (
                    destination.parent / item.linkname
                    if item.issym()
                    else target.joinpath(*link.parts)
                ).resolve()
                try:
                    source.relative_to(target.resolve())
                except ValueError as exc:
                    raise ContractError('Python archive link escapes its runtime') from exc
                destination.parent.mkdir(parents=True, exist_ok=True)
                if item.issym():
                    destination.symlink_to(item.linkname)
                elif source.is_file():
                    os.link(source, destination)
                else:
                    raise ContractError('invalid Python archive hard link')
