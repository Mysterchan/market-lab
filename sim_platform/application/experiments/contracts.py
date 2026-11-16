"""Values and injected services used while Platform assembles an Agent run.

These types document in-process setup. They are not IPC envelopes, persisted
objects or an alternative public Platform entrypoint.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Protocol, TypedDict, TYPE_CHECKING

if TYPE_CHECKING:
    from sim_platform.application.api_types import ArchiveReceipt
    from sim_platform.agents.operation_log import AgentOperationLog
    from sim_platform.execution.session import ExecutionSession
    from sim_platform.execution.models import ExecutionSnapshot
    from sim_platform.experiments.execution.session import ExperimentSession
    from sim_platform.experiments.scheduling.host import ExperimentRuntimeHandle
    from sim_platform.market.models import MarketSnapshot


class SourceWorkspace(Protocol):
    """A temporary copy of Agent source; whoever holds the launch closes it."""

    directory: Path

    def close(self) -> None: ...


class _AgentLaunch(TypedDict):
    executable: str
    project: str
    workspace: SourceWorkspace


class AgentLaunch(_AgentLaunch, total=False):
    """Selected child executable/source; the host takes ownership of workspace.

    ``sandbox`` ({'kind', 'network'}) is present when the Agent runs in an OS sandbox.
    """

    sandbox: Dict[str, str]


class AgentBinding(TypedDict):
    """A created Agent run: its run, prepared experiment, maker and Agent configuration."""

    run_id: str
    experiment_id: str
    maker_id: str
    config: dict


class ToolCapture(TypedDict, total=False):
    """Detached capture; order versions are absent on immutable-market reads."""

    market: MarketSnapshot
    execution: ExecutionSnapshot
    history: Any
    order_versions: dict


@dataclass(frozen=True)
class AgentAssemblyServices:
    """Platform-owned setup capabilities; returned resources belong to the runner.

    Factories may synchronously start children. create_log owns a local recorder,
    archive_log saves a sealed log, and failed_start compensates an unbound run.
    None of these callbacks grants Agent code access to App objects.
    """

    runtime_factory: Callable[..., ExperimentRuntimeHandle]
    execution_factory: Callable[..., ExecutionSession]
    compose_pipeline: Callable[..., ExperimentSession]
    directory: Path
    create_log: Callable[..., AgentOperationLog]
    archive_log: Callable[..., ArchiveReceipt]
    commit_decision: Callable[..., tuple]
    failed_start: Callable[[str, str], None]
    resolve_project: Callable[[str, dict], AgentLaunch]
    presentation_operations: Any
