"""OS-specific ownership of an Agent and its ordinary descendant processes.

This is lifecycle management for trusted local code, not a security sandbox.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Union

if TYPE_CHECKING:  # Annotation only.
    from .posix import PosixProcessFamily
    from .windows import WindowsProcessFamily


def create_process_family() -> Union[PosixProcessFamily, WindowsProcessFamily]:
    """Allocate parent-owned cleanup resources before spawning an Agent."""
    if os.name == 'nt':
        from .windows import WindowsProcessFamily

        return WindowsProcessFamily()
    if os.name == 'posix':
        from .posix import PosixProcessFamily

        return PosixProcessFamily()
    raise RuntimeError('Agent process management is unavailable on this OS')


def enter_process_family() -> None:
    """Run in the child before initialization or any user-controlled code."""
    if os.name == 'posix':
        os.setsid()
