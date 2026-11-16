"""EventRepository SQL operations; returns detached domain values."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Dict, Iterable, Iterator, List, Optional

from sqlalchemy import select, func
from sim_platform.domain.models import LedgerEvent
from sim_platform.persistence.sql.mapping import to_domain as _domain
from sim_platform.persistence.sql.models import LedgerEventRecord
from .base import STREAM_BATCH_ROWS, Repository


class EventRepository(Repository):
    """Ledger event rows (the authoritative account history); one per Session."""

    record_type = LedgerEventRecord
    model_type = LedgerEvent

    def for_account(
        self, account_id: str, through_sequence: Optional[int] = None
    ) -> List[LedgerEvent]:
        query = select(LedgerEventRecord).where(LedgerEventRecord.account_id == account_id)
        if through_sequence is not None:
            query = query.where(LedgerEventRecord.sequence <= through_sequence)
        return [
            _domain(row, LedgerEvent)
            for row in self.session.scalars(query.order_by(LedgerEventRecord.sequence))
        ]

    def find(self, account_id: str, event_id: str) -> Optional[LedgerEvent]:
        row = self.session.scalar(
            select(LedgerEventRecord).where(
                LedgerEventRecord.account_id == account_id, LedgerEventRecord.event_id == event_id
            )
        )
        return None if row is None else _domain(row, LedgerEvent)

    def for_run(
        self,
        run_id: str,
        *,
        kinds: Optional[Iterable[str]] = None,
        limit: Optional[int] = None,
    ) -> List[LedgerEvent]:
        """A run's events in order; with ``limit``, only the most recent ones."""
        query = select(LedgerEventRecord).where(LedgerEventRecord.run_id == run_id)
        if kinds is not None:
            query = query.where(LedgerEventRecord.kind.in_(kinds))
        if limit is None:
            rows = self.session.scalars(query.order_by(LedgerEventRecord.sequence)).all()
        else:
            rows = self.session.scalars(
                query.order_by(LedgerEventRecord.sequence.desc()).limit(limit)
            ).all()[::-1]
        return [_domain(row, LedgerEvent) for row in rows]

    def after(self, run_id: str, sequence: int) -> List[LedgerEvent]:
        query = (
            select(LedgerEventRecord)
            .where(LedgerEventRecord.run_id == run_id, LedgerEventRecord.sequence > sequence)
            .order_by(LedgerEventRecord.sequence)
        )
        return [_domain(row, LedgerEvent) for row in self.session.scalars(query)]

    def iter_account(self, account_id: str, through_sequence: int) -> Iterator[Dict[str, Any]]:
        """Stream an account's events up to ``through_sequence`` as dictionaries."""
        query = (
            select(LedgerEventRecord)
            .where(
                LedgerEventRecord.account_id == account_id,
                LedgerEventRecord.sequence <= through_sequence,
            )
            .order_by(LedgerEventRecord.sequence)
        )
        for row in self.session.scalars(query.execution_options(yield_per=STREAM_BATCH_ROWS)):
            yield asdict(_domain(row, LedgerEvent))

    def iter_run(self, run_id: str) -> Iterator[Dict[str, Any]]:
        query = (
            select(LedgerEventRecord)
            .where(LedgerEventRecord.run_id == run_id)
            .order_by(LedgerEventRecord.sequence)
        )
        for row in self.session.scalars(query.execution_options(yield_per=STREAM_BATCH_ROWS)):
            yield asdict(_domain(row, LedgerEvent))

    def has_run(self, run_id: str) -> bool:
        return (
            self.session.scalar(
                select(LedgerEventRecord.sequence)
                .where(LedgerEventRecord.run_id == run_id)
                .limit(1)
            )
            is not None
        )

    def by_ids(self, account_id: str, event_ids: Iterable[str]) -> List[LedgerEvent]:
        if not event_ids:
            return []
        query = select(LedgerEventRecord).where(
            LedgerEventRecord.account_id == account_id, LedgerEventRecord.event_id.in_(event_ids)
        )
        return [_domain(row, LedgerEvent) for row in self.session.scalars(query)]

    def count(self, run_id: str, kind: str) -> Optional[int]:
        return self.session.scalar(
            select(func.count())
            .select_from(LedgerEventRecord)
            .where(LedgerEventRecord.run_id == run_id, LedgerEventRecord.kind == kind)
        )

    def latest(self, run_id: str, kind: str) -> Optional[LedgerEvent]:
        values = self.for_run(run_id, kinds=(kind,), limit=1)
        return values[0] if values else None
