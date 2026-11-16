"""Separate bounded working checkpoints and research presentation state."""

from __future__ import annotations

import json
from sqlalchemy import text
from sim_platform.persistence.sql.models import LedgerItemRecord, ResearchArchiveRecord

from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:  # Annotation only.
    from sqlalchemy import Table
    from sqlalchemy.engine import Connection

revision = '0005_projections'
down_revision = '0004_incremental'


def upgrade(connection: Connection) -> None:
    cast('Table', LedgerItemRecord.__table__).create(connection)
    cast('Table', ResearchArchiveRecord.__table__).create(connection)
    # Checkpoints are converted lazily on their next write; old ledger bytes remain unchanged.
    for row in connection.execute(text('SELECT id, config, policy_state FROM runs')).mappings():
        if 'decision' not in json.loads(row['config']):
            continue
        journal = json.loads(row['policy_state'])
        if isinstance(journal, dict) and 'research_archive' in journal:
            archive = journal.pop('research_archive')
            connection.execute(
                cast('Table', ResearchArchiveRecord.__table__)
                .insert()
                .values(run_id=row['id'], data={'market': archive})
            )
            connection.execute(
                text('UPDATE runs SET policy_state=:state WHERE id=:id'),
                dict(
                    id=row['id'],
                    state=json.dumps(
                        journal, sort_keys=True, separators=(',', ':'), allow_nan=False
                    ),
                ),
            )
    connection.execute(
        text('UPDATE alembic_version SET version_num=:revision'), {'revision': revision}
    )
