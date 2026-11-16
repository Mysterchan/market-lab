"""Prepare wheel-only dependencies without importing project code or installed packages."""

from __future__ import annotations

from pathlib import Path, PurePosixPath
import os
import zipfile
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Union
from email.parser import Parser
from sim_platform.agents.preparation.errors import PreparationError, PreparationCancelled
from sim_platform.agents.preparation.process import run_command

if TYPE_CHECKING:  # Annotation only.
    from os import PathLike

# Wheel bounds: files per wheel, total expanded bytes (512 MiB), metadata bytes (1 MiB).
MAX_WHEEL_FILES = 50000
MAX_EXPANDED_BYTES = 512 * 1024 * 1024
MAX_METADATA_BYTES = 1024 * 1024
# Zip external attribute mask and value for a symbolic link.
FILE_TYPE_MASK = 0o170000
SYMLINK_TYPE = 0o120000
# Public package index used when downloads are allowed.
PYPI_INDEX_URL = 'https://pypi.org/simple'


def environment_python(
    directory: Union[str, PathLike[str]], *, platform: Optional[str] = None
) -> Path:
    """Locate a venv interpreter without starting it; directory is the venv root.

    All dependency preparation and saved-environment lookups use this rule.
    platform is injectable for layout checks; production uses the current OS.
    """
    platform = os.name if platform is None else platform
    return Path(directory) / ('Scripts/python.exe' if platform == 'nt' else 'bin/python')


def inspect_wheels(directory: Path) -> List[Dict[str, str]]:
    """Reject executable startup hooks, traversal and excessive expansion before installation."""
    packages = []
    total = 0
    for path in sorted(directory.iterdir()):
        if path.suffix != '.whl':
            raise PreparationError('UNSAFE_DEPENDENCY', 'only prebuilt wheels may be installed')
        with zipfile.ZipFile(path) as wheel:
            entries = wheel.infolist()
            if len(entries) > MAX_WHEEL_FILES:
                raise PreparationError('UNSAFE_DEPENDENCY', 'wheel contains too many files')
            metadata = []
            for item in entries:
                parts = PurePosixPath(item.filename).parts
                total += item.file_size
                if (
                    total > MAX_EXPANDED_BYTES
                    or item.filename.startswith('/')
                    or '\\' in item.filename
                    or '..' in parts
                    or item.filename.endswith('.pth')
                    or any(
                        part in ('sitecustomize.py', 'usercustomize.py', 'pip') for part in parts
                    )
                    or (item.external_attr >> 16) & FILE_TYPE_MASK == SYMLINK_TYPE
                ):
                    raise PreparationError(
                        'UNSAFE_DEPENDENCY', 'wheel contains an unsafe path or Python startup hook'
                    )
                if item.filename.endswith('.dist-info/METADATA'):
                    if item.file_size > MAX_METADATA_BYTES:
                        raise PreparationError('UNSAFE_DEPENDENCY', 'wheel metadata is too large')
                    metadata.append(Parser().parsestr(wheel.read(item).decode('utf-8')))
            if len(metadata) != 1 or not metadata[0].get('Name') or not metadata[0].get('Version'):
                raise PreparationError(
                    'UNSAFE_DEPENDENCY', 'wheel needs one valid distribution metadata record'
                )
            if any(
                '@' in value or '://' in value for value in metadata[0].get_all('Requires-Dist', [])
            ):
                raise PreparationError(
                    'UNSAFE_DEPENDENCY',
                    'wheel dependencies must use package requirements, not direct URLs',
                )
            packages.append(dict(name=metadata[0]['Name'], version=metadata[0]['Version']))
    return packages


def install_environment(
    target: Path,
    specification: Dict[str, Any],
    cancelled: Callable[[], bool],
    publish: Callable[[str], None],
    *,
    sdk_wheel: Path,
    wheelhouse: Optional[Union[str, PathLike[str]]] = None,
    allow_download: bool = False,
) -> List[Dict[str, str]]:
    """Create the selected Python venv, resolve and inspect wheels, then install without imports."""
    wheels = target / 'wheels'
    wheels.mkdir()
    common = [
        str(environment_python(target / 'python')),
        '-I',
        '-m',
        'pip',
        '--isolated',
        '--disable-pip-version-check',
        '--no-input',
    ]
    if specification['requirements'] and wheelhouse is None and not allow_download:
        raise PreparationError(
            'DEPENDENCY_SOURCE_REQUIRED', 'choose a wheelhouse or enable public package downloads'
        )
    publish('creating_environment')
    run_command(
        [specification['runtime']['executable'], '-I', '-m', 'venv', str(target / 'python')],
        cancelled,
        error_code='ENVIRONMENT_CREATE_FAILED',
    )
    if specification['requirements']:
        publish('resolving')
        command = common + [
            'download',
            '--only-binary=:all:',
            '--no-cache-dir',
            '--dest',
            str(wheels),
        ]
        if wheelhouse is not None:
            directory = Path(wheelhouse).expanduser().resolve(strict=True)
            if not directory.is_dir():
                raise PreparationError('INVALID_WHEELHOUSE', 'wheelhouse must be a local directory')
            if any(path.is_symlink() or not path.is_file() for path in directory.iterdir()):
                raise PreparationError(
                    'INVALID_WHEELHOUSE', 'wheelhouse must contain regular wheel files only'
                )
            inspect_wheels(directory)
            command += ['--no-index', '--find-links', str(directory)]
        else:
            command += ['--index-url', PYPI_INDEX_URL]
        run_command(command + specification['requirements'], cancelled)
    if cancelled():
        raise PreparationCancelled()
    packages = inspect_wheels(wheels)
    if packages:
        publish('installing')
        executable = environment_python(target / 'python')
        run_command(
            [
                str(executable),
                '-I',
                '-m',
                'pip',
                '--isolated',
                '--disable-pip-version-check',
                '--no-input',
                'install',
                '--no-index',
                '--no-compile',
                '--only-binary=:all:',
                '--find-links',
                str(wheels),
                *specification['requirements'],
            ],
            cancelled,
        )
    publish('installing_sdk')
    run_command(
        common + ['install', '--no-index', '--no-deps', '--no-compile', str(sdk_wheel)],
        cancelled,
        error_code='SDK_INSTALL_FAILED',
    )
    return packages
