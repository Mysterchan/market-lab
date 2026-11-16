"""Append-only agent storage; ORM/SQL and integrity reads stay in persistence."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sim_platform.domain.agents import AgentRevision, PreparedAgentExperiment
from sim_platform.domain.models import ContractError
from sim_platform.persistence.sql.mapping import to_domain
from sim_platform.persistence.sql.models import AgentRevisionRecord, AgentExperimentRecord


def revision_value(row: AgentRevisionRecord) -> AgentRevision:
    """Detach a revision row and check that its definition names the same Agent revision."""
    value = to_domain(row, AgentRevision)
    if (
        not isinstance(value.definition, dict)
        or value.definition.get('agent_id') != value.agent_id
        or value.definition.get('revision') != str(value.revision)
    ):
        raise ContractError('agent revision identity mismatch')
    return value


class AgentRepository:
    """Append-only Agent revisions; created per Session by Repositories."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def latest(self, agent_id: str) -> Optional[AgentRevision]:
        row = self.session.scalar(
            select(AgentRevisionRecord)
            .where(AgentRevisionRecord.agent_id == agent_id)
            .order_by(AgentRevisionRecord.revision.desc())
            .limit(1)
        )
        return revision_value(row) if row is not None else None

    def owned(
        self, agent_id: str, profile_id: str, revision: Optional[int] = None
    ) -> AgentRevision:
        """The given (default: latest) revision; raise if it is not in this profile."""
        if revision is None:
            value = self.latest(agent_id)
        else:
            row = self.session.get(AgentRevisionRecord, (agent_id, revision))
            value = revision_value(row) if row is not None else None
        if value is None or value.profile_id != profile_id:
            raise ContractError('unknown agent in this profile')
        return value

    def add(self, value: AgentRevision) -> None:
        self.session.add(AgentRevisionRecord(**asdict(value)))
        self.session.flush()

    def for_profile(
        self, profile_id: str, limit: int, after_id: Optional[str]
    ) -> List[AgentRevision]:
        """Latest revision of each Agent, ordered by ID, after ``after_id``."""
        latest = (
            select(
                AgentRevisionRecord.agent_id,
                func.max(AgentRevisionRecord.revision).label('revision'),
            )
            .where(AgentRevisionRecord.profile_id == profile_id)
            .group_by(AgentRevisionRecord.agent_id)
            .subquery()
        )
        query = select(AgentRevisionRecord).join(
            latest,
            (AgentRevisionRecord.agent_id == latest.c.agent_id)
            & (AgentRevisionRecord.revision == latest.c.revision),
        )
        if after_id is not None:
            query = query.where(AgentRevisionRecord.agent_id > after_id)
        return [
            revision_value(row)
            for row in self.session.scalars(
                query.order_by(AgentRevisionRecord.agent_id).limit(limit)
            )
        ]


class AgentExperimentRepository:
    """Append-only prepared Agent experiments; created per Session by Repositories."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, value: PreparedAgentExperiment) -> None:
        self.session.add(AgentExperimentRecord(**asdict(value)))
        self.session.flush()

    def owned(self, experiment_id: str, profile_id: str) -> PreparedAgentExperiment:
        """The experiment, checked against its run, account and saved revision."""
        row = self.session.get(AgentExperimentRecord, experiment_id)
        if row is None or row.profile_id != profile_id:
            raise ContractError('unknown agent experiment in this profile')
        value = to_domain(row, PreparedAgentExperiment)
        definition = value.config.get('definition') if isinstance(value.config, dict) else None
        if (
            not isinstance(definition, dict)
            or value.config.get('run_id') != value.id
            or definition.get('agent_id') != value.agent_id
            or definition.get('revision') != str(value.revision)
        ):
            raise ContractError('agent experiment identity mismatch')
        if (
            not isinstance(value.initial_snapshot, dict)
            or value.initial_snapshot.get('account_id') != value.account_id
            or value.initial_snapshot.get('profile_id') != value.profile_id
        ):
            raise ContractError('agent experiment account identity mismatch')
        revision = self.session.get(AgentRevisionRecord, (value.agent_id, value.revision))
        if (
            revision is None
            or revision.profile_id != value.profile_id
            or revision_value(revision).definition != definition
        ):
            raise ContractError('agent experiment definition differs from its saved revision')
        return value

    def for_profile(
        self, profile_id: str, limit: int, after_id: Optional[str]
    ) -> List[Dict[str, Any]]:
        """Experiment summaries ('id', 'account_id', 'agent_id', 'revision') after ``after_id``."""
        query = select(
            AgentExperimentRecord.id,
            AgentExperimentRecord.account_id,
            AgentExperimentRecord.agent_id,
            AgentExperimentRecord.revision,
        ).where(AgentExperimentRecord.profile_id == profile_id)
        if after_id is not None:
            query = query.where(AgentExperimentRecord.id > after_id)
        return [
            dict(row)
            for row in self.session.execute(
                query.order_by(AgentExperimentRecord.id).limit(limit)
            ).mappings()
        ]
