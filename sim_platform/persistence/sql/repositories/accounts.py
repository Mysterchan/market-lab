"""AccountRepository SQL operations; returns detached domain values."""

from __future__ import annotations

from typing import List, cast

from sqlalchemy import select
from sim_platform.domain.models import SimulationAccount
from sim_platform.persistence.sql.mapping import to_domain as _domain
from sim_platform.persistence.sql.models import AccountRecord
from .base import Repository


class AccountRepository(Repository):
    """Account rows; created per Session by Repositories."""

    record_type = AccountRecord
    model_type = SimulationAccount

    def owned(self, key: str, profile_id: str) -> SimulationAccount:
        """The account ``key``; raise if it belongs to another profile."""
        account = cast(SimulationAccount, self.get(key))
        account.require_owner(profile_id)
        return account

    def for_profile(self, profile_id: str) -> List[SimulationAccount]:
        return [
            _domain(row, SimulationAccount)
            for row in self.session.scalars(
                select(AccountRecord)
                .where(AccountRecord.profile_id == profile_id)
                .order_by(AccountRecord.id)
            )
        ]
