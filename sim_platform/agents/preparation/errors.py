"""Named failures shared by project, interpreter and dependency preparation."""

from __future__ import annotations

from sim_platform.domain.models import ContractError


class PreparationError(ContractError):
    """A named preparation failure; ``code`` (for example 'PYTHON_UNAVAILABLE') is reported."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class PreparationCancelled(Exception):
    """Raised inside a preparation job when its caller cancelled it."""
