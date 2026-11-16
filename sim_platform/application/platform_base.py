"""Shared state that the Platform method groups rely on.

Platform is assembled from several method groups (records, Agents, credentials) so
that no single module holds the whole public surface. Each group inherits this base,
which declares the attributes Platform.__init__ sets. It holds no behaviour of its own.
"""

from __future__ import annotations
from os import PathLike
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Union

if TYPE_CHECKING:
    from sim_platform.agents.runtimes.manager import PythonRuntimes
    from sim_platform.models.credentials import CredentialStore
    from sim_platform.persistence.agents.sdk import SDKFiles
    from .agents.environments import AgentEnvironments
    from .agents.projects import AgentProjects
    from .execution_operations import ApplicationOperations
    from .lineage import RunLineage
    from .resources import PlatformResources

PathInput = Union[str, PathLike[str]]


class PlatformBase:
    """Attributes set by Platform.__init__ and shared by every method group."""

    directory: Path
    _credentials: Optional[CredentialStore]
    _resources: PlatformResources
    _operations: ApplicationOperations
    _agent_projects: AgentProjects
    _agent_environments: AgentEnvironments
    _agent_sdk: SDKFiles
    _python_runtimes: PythonRuntimes
    _lineage: RunLineage
