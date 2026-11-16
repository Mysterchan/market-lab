"""Compute original-operation Replay once; the player only displays its events."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from sim_platform.domain.models import ContractError

if TYPE_CHECKING:  # Annotation only.
    from sim_platform.application.platform import Platform
    from sim_platform.research.jobs import Cancellation


class RecordedReplay:
    """Desktop task: replay a sealed Agent log through the Platform (runs as a job)."""

    def __init__(self, platform: Platform) -> None:
        self.platform = platform

    def prepare(
        self,
        *,
        profile: str,
        path: str,
        cancellation: Cancellation,
        account: Optional[str] = None,
    ) -> Any:
        if cancellation.cancelled:
            raise ContractError('Replay preparation cancelled')
        return self.platform.replay_prepared_operations(
            profile, account, path, cancelled=lambda: cancellation.cancelled
        )
