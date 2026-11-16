"""Legacy name; reset remains owned by the app lifecycle."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional, Tuple

if TYPE_CHECKING:  # Annotation only.
    from sim_platform.application.lifecycle import PlatformLifecycle
    from sim_platform.research.controller import ResearchController


def reset_storage(
    lifecycle: PlatformLifecycle,
    library: Optional[Any] = None,
    demo_source: Optional[Tuple[Any, Any]] = None,
) -> ResearchController:
    return lifecycle.reset(library=library, demo_source=demo_source)
