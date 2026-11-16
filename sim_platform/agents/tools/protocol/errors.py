"""Named tool errors shared by local tool objects and platform validation."""

from __future__ import annotations

from sim_platform.domain.models import ContractError


class ToolError(ContractError):
    """A tool failure with a stable wire ``code`` (for example 'INVALID_ARGUMENT').

    Raised by tool validation and handlers; tool error responses carry the code.
    """

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)
