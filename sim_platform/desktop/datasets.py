"""Legacy name; callers provide a Platform owning the dataset library."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional, Tuple

if TYPE_CHECKING:  # Annotation only.
    from sim_platform.research.datasets import DatasetLibrary as Library


def DatasetLibrary(platform: Any, demo_source: Optional[Tuple[Any, Any]] = None) -> Library:
    """``platform`` must be a sim_platform.Platform (checked at run time)."""
    from sim_platform import Platform
    from sim_platform.domain.models import ContractError

    if not isinstance(platform, Platform):
        raise ContractError(
            'DatasetLibrary requires an existing Platform; use Platform.open_datasets()'
        )
    return platform.open_datasets(demo_source=demo_source)
