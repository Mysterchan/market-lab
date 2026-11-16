"""Bounded Windows project reads without POSIX directory-descriptor APIs.

Reject reparse points and compare file/directory identity before and after reading.
This captures trusted local source; it is not isolation from hostile concurrent writers.
"""

from __future__ import annotations

import os
from pathlib import Path
import stat
from typing import TYPE_CHECKING, Dict, List, Tuple, Union

from sim_platform.domain.models import ContractError
from .projects import (
    IGNORED,
    TEXT_SUFFIXES,
    MAX_FILES,
    MAX_NESTING,
    MAX_PROJECT_BYTES,
    CapturedProject,
    private_file,
    read_text,
)

if TYPE_CHECKING:  # Annotation only.
    from os import PathLike


# FILE_ATTRIBUTE_REPARSE_POINT on Windows.
REPARSE_POINT_ATTRIBUTE = 0x400


def _details(path: Path) -> os.stat_result:
    """lstat(), refusing symlinks and Windows reparse points."""
    details = path.lstat()
    if (
        stat.S_ISLNK(details.st_mode)
        or getattr(details, 'st_file_attributes', 0) & REPARSE_POINT_ATTRIBUTE
    ):
        raise ContractError('project contains a symlink or reparse point: ' + str(path))
    return details


def _identity(details: os.stat_result) -> Tuple[int, int, int, int]:
    return details.st_dev, details.st_ino, details.st_size, details.st_mtime_ns


def _read_file(path: Path) -> str:
    before = _details(path)
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_BINARY', 0))
    try:
        opened = os.fstat(descriptor)
        text = read_text(descriptor)
        # Compare handle and path observations separately: Windows Python versions
        # can expose different metadata through their two underlying stat APIs.
        if _identity(os.fstat(descriptor)) != _identity(opened):
            raise ContractError('project file changed during capture')
    finally:
        os.close(descriptor)
    if _identity(_details(path)) != _identity(before):
        raise ContractError('project file changed during capture')
    return text


def _walk(directory: Path, prefix: str, files: Dict[str, str], excluded: List[str]) -> None:
    if prefix.count('/') > MAX_NESTING:
        raise ContractError('project nesting exceeds 16 folders')
    before = _details(directory)
    with os.scandir(directory) as entries:
        names = sorted(entry.name for entry in entries)
    for name in names:
        if len(files) + len(excluded) >= MAX_FILES:
            raise ContractError('project inventory exceeds 500 entries')
        relative, path = prefix + name, directory / name
        if name in IGNORED or private_file(name):
            excluded.append(relative)
            continue
        details = _details(path)
        if stat.S_ISDIR(details.st_mode):
            _walk(path, relative + '/', files, excluded)
        elif stat.S_ISREG(details.st_mode):
            if path.suffix.lower() not in TEXT_SUFFIXES:
                raise ContractError('unsupported project file: ' + relative)
            files[relative] = _read_file(path)
            if sum(len(value.encode('utf-8')) for value in files.values()) > MAX_PROJECT_BYTES:
                raise ContractError('project exceeds 50 MiB')
        else:
            raise ContractError('project contains a special file: ' + relative)
    if _identity(_details(directory)) != _identity(before):
        raise ContractError('project directory changed during capture')


def capture_project(source: Union[str, PathLike[str]]) -> CapturedProject:
    source = Path(source).expanduser()
    details = _details(source)
    if stat.S_ISREG(details.st_mode):
        if source.suffix != '.py':
            raise ContractError('single-file Agent source must be Python')
        return {'agent.py': _read_file(source)}, []
    if not stat.S_ISDIR(details.st_mode):
        raise ContractError('project source must be a file or directory')
    files: Dict[str, str] = {}
    excluded: List[str] = []
    _walk(source, '', files, excluded)
    return files, sorted(excluded)
