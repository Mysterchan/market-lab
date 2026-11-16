"""DecisionMakerRepository SQL operations; returns detached domain values."""

from __future__ import annotations

from typing import List, cast

from sqlalchemy import select
from sim_platform.domain.models import DecisionMaker
from sim_platform.persistence.sql.mapping import to_domain as _domain
from sim_platform.persistence.sql.models import DecisionMakerRecord
from .base import Repository


class DecisionMakerRepository(Repository):
    """Decision maker rows; created per Session by Repositories."""

    record_type = DecisionMakerRecord
    model_type = DecisionMaker

    def owned(self, key: str, profile_id: str) -> DecisionMaker:
        """The maker ``key``; raise if it belongs to another profile."""
        maker = cast(DecisionMaker, self.get(key))
        maker.require_owner(profile_id)
        return maker

    def for_profile(self, profile_id: str) -> List[DecisionMaker]:
        return [
            _domain(row, DecisionMaker)
            for row in self.session.scalars(
                select(DecisionMakerRecord)
                .where(DecisionMakerRecord.profile_id == profile_id)
                .order_by(DecisionMakerRecord.id)
            )
        ]
