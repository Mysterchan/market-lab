"""Optional operating-system sandbox around a saved Agent's own Python process.

An imported Agent is user code. By default it runs as an ordinary child process with
the user's permissions. With a sandbox it runs inside one of:

- ``podman`` (Linux): a rootless container that uses the host's root filesystem read
  only (``--rootfs /``), with only the Agent's work folder and a private temporary
  folder writable, no other devices, and the platform's pipes passed in by number
  (``--preserve-fd``). Docker cannot pass open pipes into a container, so it is not a
  supported runtime.
- ``bubblewrap`` (Linux): the same restrictions with user namespaces, without a
  container engine. On Linux the host's ``/dev/shm`` (shared memory, where the
  platform's interprocess locks live) stays writable inside the sandbox.
- ``macos`` (macOS): ``sandbox-exec`` with a profile that denies writing outside the
  work folder and temporary folders.

The network policy is ``none`` (no network at all: use with a model provider that runs
in the App, or a rehearsal) or ``model`` (network allowed, for an Agent that calls its
model itself). A ``local`` policy allows only loopback, for a model server on this
machine; it needs ``macos`` or ``bubblewrap``/``podman`` with the host's loopback.

The sandbox wraps the interpreter command only; the platform's IPC, scheduling and
financial checks are unchanged, so results are identical with and without it.
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import sys
from typing import Any, Dict, List, Optional, Sequence

from sim_platform.domain.models import ContractError

KINDS = ('none', 'auto', 'podman', 'bubblewrap', 'macos')
NETWORK = ('none', 'local', 'model')


def validate(sandbox: Any) -> Optional[Dict[str, str]]:
    """Check an Agent setting ``sandbox`` ({'kind', 'network'}); None means no sandbox."""
    if sandbox is None:
        return None
    if (
        not isinstance(sandbox, dict)
        or set(sandbox) - {'kind', 'network'}
        or sandbox.get('kind', 'auto') not in KINDS
        or sandbox.get('network', 'none') not in NETWORK
    ):
        raise ContractError(
            f"sandbox takes kind ({', '.join(KINDS)}) and network ({', '.join(NETWORK)})"
        )
    value = dict(kind=sandbox.get('kind', 'auto'), network=sandbox.get('network', 'none'))
    return None if value['kind'] == 'none' else value


def _podman_passes_descriptors() -> bool:
    """Whether this Podman can pass chosen open descriptors in (--preserve-fd)."""
    import subprocess

    try:
        result = subprocess.run(
            ['podman', 'run', '--help'], capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return '--preserve-fd ' in result.stdout or '--preserve-fd=' in result.stdout


def available() -> List[str]:
    """Sandbox kinds this machine can run."""
    found = []
    if sys.platform == 'darwin' and shutil.which('sandbox-exec'):
        found.append('macos')
    if sys.platform.startswith('linux'):
        if shutil.which('podman') and _podman_passes_descriptors():
            found.append('podman')
        if shutil.which('bwrap'):
            found.append('bubblewrap')
    return found


def resolve(sandbox: Dict[str, str]) -> Dict[str, str]:
    """Choose a concrete kind for 'auto' and check it is installed."""
    kinds = available()
    kind = sandbox['kind']
    if kind == 'auto':
        if not kinds:
            raise ContractError('no Agent sandbox is available on this machine')
        kind = kinds[0]
    if kind not in kinds:
        raise ContractError(f'the {kind} sandbox is not available on this machine')
    return dict(sandbox, kind=kind)


def _macos_profile(writable: Sequence[str], network: str) -> str:
    rules = [
        '(version 1)',
        '(allow default)',
        '(deny file-write*)',
        '(allow file-write* (literal "/dev/null") (literal "/dev/dtracehelper"))',
    ]
    for path in writable:
        rules.append(f'(allow file-write* (subpath "{os.path.realpath(path)}"))')
    if network != 'model':
        rules.append('(deny network-outbound)')
        rules.append('(deny network-inbound)')
        if network == 'local':
            rules.append('(allow network-outbound (remote ip "localhost:*"))')
        # Unix sockets stay allowed: Python's own runtime uses them.
        rules.append('(allow network-outbound (remote unix-socket))')
    return '\n'.join(rules)


def wrap(
    command: List[str],
    sandbox: Dict[str, str],
    *,
    workspace: str,
    descriptors: Sequence[int],
) -> List[str]:
    """``command`` run inside ``sandbox``, with ``workspace`` writable.

    ``descriptors`` are the open pipe numbers the child must keep (the platform's IPC).
    The caller creates ``workspace/.tmp``, the sandboxed process's temporary folder.
    """
    kind, network = sandbox['kind'], sandbox['network']
    # The Agent's only writable places: its work folder and a temporary folder inside it.
    temporary = os.path.join(workspace, '.tmp')  # created by the caller
    if kind == 'macos':
        profile = _macos_profile([workspace], network)
        return ['sandbox-exec', '-p', profile, '/usr/bin/env', f'TMPDIR={temporary}', *command]
    if kind == 'bubblewrap':
        # The host's tree stays visible read only (the Agent's Python and SDK may live
        # under /tmp); only the work folder is writable and TMPDIR points inside it.
        wrapped = [
            'bwrap',
            '--die-with-parent',
            '--unshare-all',
            '--ro-bind',
            '/',
            '/',
            '--dev',
            '/dev',
            # multiprocessing's semaphores (the platform's own locks) live in /dev/shm.
            '--bind',
            '/dev/shm',
            '/dev/shm',
            '--proc',
            '/proc',
            '--bind',
            workspace,
            workspace,
            '--setenv',
            'TMPDIR',
            temporary,
        ]
        if network != 'none':
            wrapped.append('--share-net')
        return [*wrapped, '--', *command]
    if kind == 'podman':
        wrapped = [
            'podman',
            'run',
            '--rm',
            '--interactive',
            '--rootfs',
            '/',
            '--read-only',
            '--env',
            f'TMPDIR={temporary}',
            '--volume',
            f'{workspace}:{workspace}:rw',
            '--volume',
            '/dev/shm:/dev/shm:rw',
            '--workdir',
            workspace,
            '--network',
            'none' if network == 'none' else 'host',
            '--userns',
            'keep-id',
            '--preserve-fd',
            ','.join(str(fd) for fd in sorted(set(descriptors))),
        ]
        return [*wrapped, *command]
    raise ContractError(f'unknown sandbox {kind!r}')


def describe(sandbox: Optional[Dict[str, str]]) -> Dict[str, Any]:
    """What a run records about its sandbox."""
    if sandbox is None:
        return dict(kind='none')
    return dict(sandbox, workspace_only_writes=True)


def workspace_for(project: str) -> str:
    """The folder a sandboxed Agent may write: its writable source copy."""
    return str(Path(project).resolve())
