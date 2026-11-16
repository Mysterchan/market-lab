"""Add execution recovery state without rewriting any historical ledger bytes."""

from __future__ import annotations

from sqlalchemy import text
from sim_platform.persistence.sql.models import RunCheckpointRecord

from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:  # Annotation only.
    from sqlalchemy import Table
    from sqlalchemy.engine import Connection

revision = '0006_runtime'
down_revision = '0005_projections'


def upgrade(connection: Connection) -> None:
    cast('Table', RunCheckpointRecord.__table__).create(connection)
    connection.execute(
        text('UPDATE alembic_version SET version_num=:revision'), {'revision': revision}
    )
