"""MakerService operations within one caller-owned transaction."""

from __future__ import annotations

from typing import Any, Optional

from sim_platform.domain.models import ContractError, DecisionMaker, label
from sim_platform.domain.json_values import copy_json
from sim_platform.application.identifiers import identifier


# The unit of work's persistence Repositories; use cases may not import persistence.
Repositories = Any

# Decision maker kinds a run may register.
MAKER_KINDS = ("human", "rule", "model", "agent")


class MakerService:
    """Decision maker registration inside the caller's unit of work."""

    def __init__(self, repositories: Repositories) -> None:
        self.repo = repositories

    def create_maker(
        self, profile_id: str, name: str, kind: str, version: str, config: Optional[Any] = None
    ) -> str:
        """Register a maker in the profile and return its new ID."""
        label(name, "maker name")
        label(version, "maker version")
        if kind not in MAKER_KINDS:
            raise ContractError("unknown decision maker kind")
        self.repo.profiles.get(profile_id)
        maker = DecisionMaker(
            identifier(),
            profile_id,
            name,
            kind,
            version,
            copy_json({} if config is None else config),
        )
        self.repo.makers.add(maker)
        return maker.id
