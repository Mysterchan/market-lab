"""ProfileRepository SQL operations; returns detached domain values."""

from __future__ import annotations

from typing import List

from sqlalchemy import select
from sim_platform.domain.models import Profile
from sim_platform.persistence.sql.mapping import to_domain as _domain
from sim_platform.persistence.sql.models import ProfileRecord
from .base import Repository


class ProfileRepository(Repository):
    """Profile rows; created per Session by Repositories."""

    record_type = ProfileRecord
    model_type = Profile

    def all(self) -> List[Profile]:
        return [
            _domain(row, Profile)
            for row in self.session.scalars(select(ProfileRecord).order_by(ProfileRecord.id))
        ]
