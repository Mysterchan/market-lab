"""RunRepository SQL operations; returns detached domain values."""

from __future__ import annotations

from typing import Any, Dict, Iterator, List, Optional

from sqlalchemy import select
from sim_platform.domain.json_values import copy_json
from sim_platform.domain.models import ContractError, Run
from sim_platform.persistence.sql.mapping import to_domain as _domain
from sim_platform.persistence.sql.decision_records import DecisionRecordCodec
from sim_platform.persistence.sql.models import (
    AccountRecord,
    RunRecord,
    ResearchArchiveRecord,
    DecisionRecord,
    RunCheckpointRecord,
)
from .base import STREAM_BATCH_ROWS, Repository

# Decision summaries returned when no limit is given.
DEFAULT_SUMMARY_LIMIT = 100

# A decision record as stored in a run journal ('index', 'maker_id', 'observation', ...).
DecisionRecordData = Dict[str, Any]


class RunRepository(Repository):
    """Run rows and their decision records, archives and checkpoints; one per Session."""

    record_type = RunRecord
    model_type = Run

    def journal(self, run_id: str, *, include_records: bool = True) -> Any:
        """The run's policy state with its research archive and, optionally, its records."""
        state = copy_json(self.row(RunRecord, run_id).policy_state)
        archive = self.row(ResearchArchiveRecord, run_id)
        if archive is not None and 'market' in archive.data:
            state['research_archive'] = copy_json(archive.data.get('market', {}))
        if include_records and isinstance(state, dict) and '_record_count' in state:
            state.pop('_record_count')
            state['records'] = self.records(run_id)
        return state

    def archive(self, run_id: str) -> Optional[Dict[str, Any]]:
        row = self.session.get(ResearchArchiveRecord, run_id)
        return copy_json(row.data) if row is not None else None

    def save_archive(self, run_id: str, data: Any) -> None:
        row = self.row(ResearchArchiveRecord, run_id)
        if row is None:
            self.session.add(ResearchArchiveRecord(run_id=run_id, data={'market': copy_json(data)}))
        else:
            row.data = {**row.data, 'market': copy_json(data)}
        self.session.flush()

    def save_final(self, run_id: str, snapshot: Any) -> None:
        row = self.row(ResearchArchiveRecord, run_id)
        if row is None:
            self.session.add(
                ResearchArchiveRecord(run_id=run_id, data={'final': copy_json(snapshot)})
            )
        else:
            row.data = {**row.data, 'final': copy_json(snapshot)}
        self.session.flush()

    def final(self, run_id: str) -> Any:
        row = self.row(ResearchArchiveRecord, run_id)
        return copy_json(row.data.get('final')) if row else None

    def save_agent_operations(self, run_id: str, archive: Any) -> None:
        """Attach completed operations without replacing market/final/execution data."""
        row = self.row(ResearchArchiveRecord, run_id)
        data = {} if row is None else row.data
        existing = data.get('agent_operations')
        if existing is not None:
            if existing != archive:
                raise ContractError('run already has a different agent operation archive')
            return
        updated = dict(data, agent_operations=copy_json(archive))
        if row is None:
            self.session.add(ResearchArchiveRecord(run_id=run_id, data=updated))
        else:
            row.data = updated
        self.session.flush()

    def records(self, run_id: str, limit: Optional[int] = None) -> List[DecisionRecordData]:
        query = select(DecisionRecord).where(DecisionRecord.run_id == run_id)
        if limit is None:
            rows = self.session.scalars(query.order_by(DecisionRecord.decision_index)).all()
        else:
            rows = self.session.scalars(
                query.order_by(DecisionRecord.decision_index.desc()).limit(limit)
            ).all()[::-1]
        codec = DecisionRecordCodec(self.session, run_id)
        return [codec.expand(row.data) for row in rows]

    def record_summaries(
        self, run_id: str, limit: int = DEFAULT_SUMMARY_LIMIT, after_index: int = 0
    ) -> List[Dict[str, Any]]:
        rows = self.session.scalars(
            select(DecisionRecord)
            .where(DecisionRecord.run_id == run_id, DecisionRecord.decision_index > after_index)
            .order_by(DecisionRecord.decision_index.desc())
            .limit(limit)
        ).all()[::-1]
        values = []
        for row in rows:
            data = row.data
            obs = data['observation']
            values.append(
                dict(
                    index=data['index'],
                    maker_id=data.get('maker_id'),
                    generation=data.get('generation', 0),
                    fingerprint=data['fingerprint'],
                    status=data['status'],
                    error=data['error'],
                    results=copy_json(data.get('results', [])),
                    observation=dict(
                        boundary=obs['boundary'],
                        trigger=obs['trigger'],
                        market=dict(time_ns=obs['market']['time_ns']),
                    ),
                    summary=True,
                )
            )
        return values

    def iter_records(self, run_id: str) -> Iterator[DecisionRecordData]:
        query = (
            select(DecisionRecord)
            .where(DecisionRecord.run_id == run_id)
            .order_by(DecisionRecord.decision_index)
        )
        codec = DecisionRecordCodec(self.session, run_id)
        for row in self.session.scalars(query.execution_options(yield_per=STREAM_BATCH_ROWS)):
            yield codec.expand(row.data)

    def append_record(self, run_id: str, record: DecisionRecordData) -> None:
        self.row(RunRecord, run_id)
        data = DecisionRecordCodec(self.session, run_id).compact(record)
        self.session.add(DecisionRecord(run_id=run_id, decision_index=record['index'], data=data))
        self.session.flush()

    def execution_state(self, run_id: str) -> Optional[Dict[str, Any]]:
        row = self.row(RunCheckpointRecord, run_id)
        if row is None:
            return None
        if row.version != 1:
            raise ContractError('unsupported run checkpoint version')
        from sim_platform.persistence.sql.run_checkpoints import validate

        validate(row.state)
        return copy_json(row.state)

    def save_execution_state(self, run_id: str, state: Dict[str, Any]) -> None:
        row = self.row(RunCheckpointRecord, run_id)
        if row is None:
            self.session.add(RunCheckpointRecord(run_id=run_id, version=1, state=copy_json(state)))
        else:
            row.state = copy_json(state)
        self.advance_time(run_id, state['market']['time_ns'])

    def save_execution_archive(self, run_id: str, state: Dict[str, Any]) -> None:
        row = self.row(ResearchArchiveRecord, run_id)
        if row is None:
            self.session.add(
                ResearchArchiveRecord(run_id=run_id, data={'execution': copy_json(state)})
            )
        else:
            row.data = {**row.data, 'execution': copy_json(state)}

    def active_for_account(self, account_id: str) -> bool:
        return (
            self.session.scalar(
                select(RunRecord.id).where(
                    RunRecord.account_id == account_id, RunRecord.status == "running"
                )
            )
            is not None
        )

    def for_profile(self, profile_id: str) -> List[Run]:
        statement = (
            select(RunRecord)
            .join(AccountRecord)
            .where(AccountRecord.profile_id == profile_id)
            .order_by(RunRecord.id)
        )
        return [_domain(row, Run) for row in self.session.scalars(statement)]

    def save_config(self, run_id: str, config: Any) -> None:
        self.row(RunRecord, run_id).config = copy_json(config)
        self.session.flush()

    def set_status(self, run_id: str, status: str) -> None:
        row = self.row(RunRecord, run_id)
        row.status = status
        self.session.flush()

    def advance_time(self, run_id: str, time_ns: int) -> None:
        row = self.row(RunRecord, run_id)
        row.time_ns = max(row.time_ns, time_ns)
        # Flush in this transaction, after event insertion, so a failure rolls back both.
        self.session.flush()

    def save_policy_state(self, run_id: str, state: Any) -> None:
        self.row(RunRecord, run_id).policy_state = copy_json(state)
        self.session.flush()

    def set_decision_owner(self, run_id: str, maker_id: str, config: Any, state: Any) -> None:
        row = self.row(RunRecord, run_id)
        row.maker_id = maker_id
        row.config = copy_json(config)
        row.policy_state = copy_json(state)
        self.session.flush()
