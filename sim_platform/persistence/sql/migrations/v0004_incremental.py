"""Add derived checkpoints and split decision audit from mutable policy state."""

from __future__ import annotations

import json
from sqlalchemy import Column, Text, Integer, ForeignKey, CheckConstraint, text
from alembic.migration import MigrationContext
from alembic.operations import Operations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # Annotation only.
    from sqlalchemy.engine import Connection

revision = '0004_incremental'
down_revision = '0003_execution'


def upgrade(connection: Connection) -> None:
    operations = Operations(MigrationContext.configure(connection))
    operations.create_table(
        'account_checkpoints',
        Column('account_id', Text, ForeignKey('accounts.id'), primary_key=True),
        Column('state', Text, nullable=False),
    )
    records = operations.create_table(
        'decision_records',
        Column('run_id', Text, ForeignKey('runs.id'), primary_key=True),
        Column('decision_index', Integer, primary_key=True),
        Column('data', Text, nullable=False),
        CheckConstraint('decision_index > 0', name='decision_index'),
    )
    # Checkpoints are built lazily from existing events on the first account write.
    # Existing ledger rows and fixture-policy JSON bytes are never changed.
    for row in (
        connection.execute(text('SELECT id, config, policy_state FROM runs')).mappings().all()
    ):
        configuration = json.loads(row['config'])
        if not isinstance(configuration, dict) or 'decision' not in configuration:
            continue
        journal = json.loads(row['policy_state'])
        if not journal:
            continue
        audit = journal.pop('records', [])
        for index, record in enumerate(audit, 1):
            if record.get('index') != index:
                raise ValueError('invalid historical decision audit index')
            connection.execute(
                records.insert().values(
                    run_id=row['id'],
                    decision_index=index,
                    data=json.dumps(record, sort_keys=True, separators=(',', ':'), allow_nan=False),
                )
            )
        journal['_record_count'] = len(audit)
        connection.execute(
            text('UPDATE runs SET policy_state=:state WHERE id=:id'),
            dict(
                id=row['id'],
                state=json.dumps(journal, sort_keys=True, separators=(',', ':'), allow_nan=False),
            ),
        )
    connection.execute(
        text('UPDATE alembic_version SET version_num=:revision'), {'revision': revision}
    )
