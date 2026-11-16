"""Add cancellation/execution audit event kinds; preserve all existing rows and balances."""

from __future__ import annotations

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # Annotation only.
    from sqlalchemy.engine import Connection

revision = '0003_execution'
down_revision = '0002_orm'


def upgrade(connection: Connection) -> None:
    operations = Operations(MigrationContext.configure(connection))
    with operations.batch_alter_table('events', recreate='always') as batch:
        batch.drop_constraint('event_kind', type_='check')
        batch.create_check_constraint(
            'event_kind',
            "kind IN ('opened','intent','accepted','rejected','fill','cancelled','execution_tick','execution_end')",
        )
    connection.execute(
        text('UPDATE alembic_version SET version_num=:revision'), {'revision': revision}
    )
