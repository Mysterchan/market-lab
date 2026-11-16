"""Legacy name accepting an existing Platform; never owns the application root."""

from __future__ import annotations

from typing import Any, Optional


class ResearchSession:
    """Legacy wrapper that forwards to a ResearchController opened on a caller's Platform.

    The caller owns the Platform; this object owns only the controller it opened and
    closes it in close(). Attribute reads are delegated to that controller.
    """

    def __init__(self, platform: Any, library: Optional[Any] = None) -> None:
        # platform: the caller's sim_platform.Platform (application package, typed later);
        # library: optional path to the compiled market-data library.
        from sim_platform import Platform
        from sim_platform.domain.models import ContractError

        if not isinstance(platform, Platform):
            raise ContractError(
                'ResearchSession requires an existing Platform; use Platform.open_research()'
            )
        self.app = platform
        self._controller = platform.open_research(library=library)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._controller, name)

    def close(self) -> None:
        self._controller.close()
