"""Detached append-only agent definitions and prepared experiment snapshots."""

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentRevision:
    agent_id: str
    revision: int
    profile_id: str
    definition: dict


@dataclass(frozen=True)
class PreparedAgentExperiment:
    id: str
    profile_id: str
    account_id: str
    agent_id: str
    revision: int
    config: dict
    initial_snapshot: dict
    execution_config: dict
