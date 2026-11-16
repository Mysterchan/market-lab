"""Lifetime account lock shared by Platform instances and processes."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Union

from sim_platform.persistence.locking import acquire_exclusive, release_exclusive
from sim_platform.domain.models import ContractError

if TYPE_CHECKING:  # Annotation only.
    from os import PathLike


class AccountLease:
    """An exclusive file lock showing that this process executes an account.

    Taken when execution starts and held until close(); the lock is also released
    if the process exits.
    """

    def __init__(self, directory: Union[str, PathLike[str]], account_id: str) -> None:
        root = Path(directory) / 'execution-locks'
        root.mkdir(parents=True, exist_ok=True)
        self.stream = (root / (account_id + '.lock')).open('ab')
        try:
            acquired = acquire_exclusive(self.stream, blocking=False)
        except Exception:
            self.stream.close()
            raise
        if not acquired:
            self.stream.close()
            raise ContractError('account already has an execution owner in another instance')

    def close(self) -> None:
        if not self.stream.closed:
            release_exclusive(self.stream)
            self.stream.close()
