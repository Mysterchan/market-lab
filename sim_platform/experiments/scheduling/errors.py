"""Work capability refusals shared by validation and scheduling, without owner dependencies."""

from __future__ import annotations

from sim_platform.domain.models import ContractError


class WorkError(ContractError):
    """A refused work request with a stable ``code`` such as 'STALE_WORK' or 'PAUSED'.

    Raised by the scheduling policy and wait validation; the Runtime loop copies
    ``code`` into the error reply sent back to the Agent.
    """

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)
