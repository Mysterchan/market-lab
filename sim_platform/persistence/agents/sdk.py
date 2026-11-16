"""Build/export the pure-Python SDK wheel from an explicit trusted source inventory."""

from __future__ import annotations

import base64
import csv
import hashlib
import io
from pathlib import Path
import shutil
from tempfile import NamedTemporaryFile
import os
import zipfile
import sys
from typing import TYPE_CHECKING, Dict, Optional, Union

from sim_platform.domain.models import ContractError
from sim_platform.sdk.distribution import NAME, VERSION, FILENAME, MODULES, DIRECTORIES

if TYPE_CHECKING:  # Annotation only.
    from os import PathLike


def wheel_contents() -> Dict[str, bytes]:
    """Every file of the SDK wheel (sources, licenses, metadata and RECORD) by archive name."""
    root = Path(__file__).resolve().parents[2]
    sources = {root / (module + '.py') for module in MODULES}
    for directory in DIRECTORIES:
        sources.update((root / directory).glob('*.py'))
    values = {
        'sim_platform/' + path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(sources)
    }
    values['sim_platform/__init__.py'] = (
        b'"""Agent SDK namespace. Public imports live in sim_platform.sdk."""\n'
    )
    dist = f'{NAME}-{VERSION}.dist-info'
    values[dist + '/licenses/Python-PSF.txt'] = (root / 'sdk/licenses/Python-PSF.txt').read_bytes()
    values[dist + '/licenses/Apache-2.0.txt'] = (root.parent / 'LICENSE').read_bytes()
    values[dist + '/licenses/NOTICE'] = (root.parent / 'NOTICE').read_bytes()
    values[dist + '/METADATA'] = (
        f'Metadata-Version: 2.1\nName: {NAME}\nVersion: {VERSION}\n'
        'Requires-Python: >=3.9\nLicense: Apache-2.0\n'
        'Summary: Agent interfaces and local runtime capabilities\n'
    ).encode()
    values[dist + '/WHEEL'] = (
        b'Wheel-Version: 1.0\nGenerator: sim-platform\nRoot-Is-Purelib: true\nTag: py3-none-any\n'
    )
    record = io.StringIO(newline='')
    writer = csv.writer(record)
    for name, data in sorted(values.items()):
        # Standard wheel RECORD integrity, not a platform Agent/account identity.
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b'=').decode()
        writer.writerow((name, 'sha256=' + digest, len(data)))
    writer.writerow((dist + '/RECORD', '', ''))
    values[dist + '/RECORD'] = record.getvalue().encode()
    return values


def has_project_notices(path: Path) -> bool:
    """Refresh pre-release wheel caches created before project licensing was chosen."""
    dist = f'{NAME}-{VERSION}.dist-info/licenses/'
    try:
        with zipfile.ZipFile(path) as archive:
            return {
                dist + name for name in ('Apache-2.0.txt', 'NOTICE', 'Python-PSF.txt')
            }.issubset(archive.namelist())
    except (OSError, zipfile.BadZipFile):
        return False


def matches_sources(path: Path, contents: Dict[str, bytes]) -> bool:
    """Check development cache bytes directly; no separate identity digest is stored."""
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            return (
                len(names) == len(contents)
                and set(names) == set(contents)
                and all(archive.read(name) == data for name, data in contents.items())
            )
    except (OSError, zipfile.BadZipFile, KeyError):
        return False


class SDKFiles:
    """The Agent SDK wheel: the bundled copy in a frozen app, else built into the data folder.

    Created by Platform; it holds paths only. wheel() rebuilds the cached wheel when
    the SDK sources change.
    """

    def __init__(
        self,
        directory: Union[str, PathLike[str]],
        bundled: Optional[Union[str, PathLike[str]]] = None,
    ) -> None:
        self.root = Path(directory) / 'agents' / 'sdk'
        if bundled is None and getattr(sys, 'frozen', False):
            bundled = Path(__file__).resolve().parents[2] / 'sdk' / FILENAME
        self.bundled = Path(bundled) if bundled is not None else None

    def wheel(self) -> Path:
        if self.bundled is not None:
            if (
                not self.bundled.is_file()
                or self.bundled.name != FILENAME
                or not has_project_notices(self.bundled)
            ):
                raise ContractError(
                    'bundled SDK wheel is missing or differs from this installation'
                )
            return self.bundled
        self.root.mkdir(parents=True, exist_ok=True)
        target = self.root / FILENAME
        contents = wheel_contents()
        if target.is_file() and matches_sources(target, contents):
            return target
        temporary = None
        try:
            with NamedTemporaryFile(dir=self.root, suffix='.tmp', delete=False) as stream:
                temporary = Path(stream.name)
                with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
                    for name, data in contents.items():
                        archive.writestr(name, data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
            return target
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()

    def export(self, target: Union[str, PathLike[str]]) -> Path:
        target = Path(target).expanduser()
        if not target.is_absolute() or target.name != FILENAME:
            raise ContractError('SDK output must be an absolute path ending in ' + FILENAME)
        target.parent.mkdir(parents=True, exist_ok=True)
        source = self.wheel()
        if source.resolve() != target.resolve():
            shutil.copyfile(source, target)
        return target
