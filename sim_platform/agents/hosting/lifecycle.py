"""Startup errors and bounded teardown shared by normal and partially started hosts."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sim_platform.domain.models import ContractError

if TYPE_CHECKING:  # Annotation only.
    from multiprocessing.process import BaseProcess


class AgentStartupError(ContractError):
    """Named setup failure; messages contain no user exception, credentials or payload."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def reap_process_family(process: BaseProcess, family: Any, grace: float) -> None:
    """``family`` is the OS process family (PosixProcessFamily or WindowsProcessFamily)."""
    """Terminate descendants too, including after their Agent parent returned or crashed."""
    if process.pid is None:
        return
    family.terminate(process)
    process.join(grace)
    family.terminate(process, force=True)
    process.join(grace)
    if process.is_alive():
        raise ContractError('Agent process remains alive after forced termination')
