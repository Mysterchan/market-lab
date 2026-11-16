"""Capture bounded text projects and publish private snapshots before SQL references them."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import stat
from typing import TYPE_CHECKING, Any, Dict, List, Tuple, Union
from uuid import uuid4
from sim_platform.domain.models import ContractError
from sim_platform.persistence.export_files import atomic_write

if TYPE_CHECKING:  # Annotation only.
    from os import PathLike

IGNORED = {'.git', '.venv', 'venv', '__pycache__', 'node_modules', 'build', 'dist', '.DS_Store'}
TEXT_SUFFIXES = {'.py', '.json', '.txt', '.md', '.toml', '.csv', '.yaml', '.yml'}
MAX_FILES = 500
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_PROJECT_BYTES = 50 * 1024 * 1024
# Deepest folder nesting captured.
MAX_NESTING = 16
# Project references are 32 lower-case hex digits.
REFERENCE_PATTERN = r'[a-f0-9]{32}'

# A captured project: file texts by relative path, and the excluded paths.
CapturedProject = Tuple[Dict[str, str], List[str]]


def private_file(name: str) -> bool:
    """Whether a file name looks like credentials or keys, which are never captured."""
    lower = name.lower()
    return (
        lower == '.env'
        or lower.startswith('.env.')
        or lower in ('credentials.json', 'credentials', 'secrets.json')
        or lower.endswith(('.pem', '.key', '.p12', '.pfx'))
    )


def capture_project(source: Union[str, PathLike[str]]) -> CapturedProject:
    """Capture bounded text with the OS-specific filesystem adapter."""
    if os.name == 'nt':
        from .project_capture_windows import capture_project as capture_windows

        return capture_windows(source)
    source = Path(source).expanduser()
    files: Dict[str, str] = {}
    excluded: List[str] = []
    if source.is_symlink():
        raise ContractError('project source must not be a symlink')
    if source.is_file():
        if source.suffix != '.py':
            raise ContractError('single-file Agent source must be Python')
        descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            files['agent.py'] = read_text(descriptor)
        finally:
            os.close(descriptor)
    else:
        descriptor = os.open(source, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            walk_project(descriptor, '', files, excluded)
        finally:
            os.close(descriptor)
    return files, sorted(excluded)


def read_text(descriptor: int) -> str:
    """Decode bounded UTF-8 without changing source line endings."""
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        raise ContractError('project contains a special file')
    with os.fdopen(os.dup(descriptor), 'rb') as stream:
        data = stream.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise ContractError('project file exceeds 5 MiB')
    try:
        return data.decode('utf-8')
    except UnicodeError as exc:
        raise ContractError('project files must be UTF-8 text') from exc


def walk_project(descriptor: int, prefix: str, files: Dict[str, str], excluded: List[str]) -> None:
    """Capture a directory's allowed files into ``files``, recursing into subfolders."""
    if prefix.count('/') > MAX_NESTING:
        raise ContractError('project nesting exceeds 16 folders')
    with os.scandir(descriptor) as entries:
        names = sorted(entry.name for entry in entries)
    for name in names:
        if len(files) + len(excluded) >= MAX_FILES:
            raise ContractError('project inventory exceeds 500 entries')
        relative = prefix + name
        if name in IGNORED or private_file(name):
            excluded.append(relative)
            continue
        details = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
        if stat.S_ISLNK(details.st_mode):
            raise ContractError('project contains a symlink: ' + relative)
        if stat.S_ISDIR(details.st_mode):
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            try:
                walk_project(child, relative + '/', files, excluded)
            finally:
                os.close(child)
        elif stat.S_ISREG(details.st_mode):
            if Path(name).suffix.lower() not in TEXT_SUFFIXES or len(files) >= MAX_FILES:
                raise ContractError(
                    'unsupported project file or project exceeds 500 files: ' + relative
                )
            child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=descriptor)
            try:
                files[relative] = read_text(child)
            finally:
                os.close(child)
            if sum(len(value.encode('utf-8')) for value in files.values()) > MAX_PROJECT_BYTES:
                raise ContractError('project exceeds 50 MiB')
        else:
            raise ContractError('project contains a special file: ' + relative)


class ProjectFiles:
    """Saved private snapshots of imported Agent projects under the data folder.

    Created by Platform; it holds paths only. Each saved snapshot is referenced by
    the Agent definition that SQL stores.
    """

    def __init__(self, directory: Union[str, PathLike[str]]) -> None:
        self.root = Path(directory) / 'agents' / 'projects'

    @staticmethod
    def capture(source: Union[str, PathLike[str]]) -> CapturedProject:
        return capture_project(source)

    def directory(self, reference: Any) -> Path:
        if not isinstance(reference, str) or re.fullmatch(REFERENCE_PATTERN, reference) is None:
            raise ContractError('invalid Agent project reference; import the source project')
        return self.root / reference

    def save(self, files: Dict[str, str], report: Dict[str, Any]) -> Dict[str, Any]:
        """Write a new snapshot and its project.json; return the saved metadata."""
        reference = uuid4().hex
        target = self.directory(reference)
        target.mkdir(parents=True)
        try:
            for name, text in files.items():
                path = target / 'source' / name
                path.parent.mkdir(parents=True, exist_ok=True)
                # Text-mode writes on Windows would change the captured byte inventory.
                path.write_bytes(text.encode('utf-8'))
            metadata = dict(report, project_ref=reference)
            atomic_write(target / 'project.json', lambda stream: json.dump(metadata, stream))
            return metadata
        except Exception:
            shutil.rmtree(target)
            raise

    def read(self, reference: str, profile: str) -> Tuple[Dict[str, str], Dict[str, Any]]:
        """Read a snapshot back, checking its owner and file inventory."""
        target = self.directory(reference)
        with (target / 'project.json').open(encoding='utf-8') as stream:
            report = json.load(stream)
        if (
            type(report) is not dict
            or report.get('project_ref') != reference
            or report.get('profile_id') != profile
        ):
            raise ContractError('Agent project identity differs from its reference')
        files, excluded = capture_project(target / 'source')
        if excluded:
            raise ContractError('saved project contains unexpected private/generated files')
        actual = [
            dict(path=name, bytes=len(text.encode('utf-8'))) for name, text in sorted(files.items())
        ]
        if report.get('files') != actual:
            raise ContractError('Agent project files differ from the saved inventory')
        return files, report

    def discard(self, reference: str) -> None:
        shutil.rmtree(self.directory(reference))
