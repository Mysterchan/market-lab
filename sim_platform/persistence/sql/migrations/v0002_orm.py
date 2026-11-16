"""Upgrade the original account schema without resetting IDs, JSON, events or balances."""

from __future__ import annotations

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Column, Text

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # Annotation only.
    from sqlalchemy.engine import Connection

revision = "0002_orm"
down_revision = None  # v1 used PRAGMA user_version, not an Alembic revision table.


def upgrade(connection: Connection) -> None:
    operations = Operations(MigrationContext.configure(connection))
    # Freeze the historical revision, rather than referring to mutable current
    # ORM metadata. Future model changes must not alter how v1 -> v2 is performed.
    changes = {
        "profiles": (["id"], [], []),
        "accounts": (
            ["id"],
            [("account_currency", "currency = 'USD'")],
            [("accounts_by_profile", ["profile_id"])],
        ),
        "makers": (
            ["id"],
            [("maker_kind", "kind IN ('human','rule','model','agent')")],
            [("makers_by_profile", ["profile_id"])],
        ),
        "runs": (
            ["id"],
            [
                ("run_status", "status IN ('running','completed','stopped','failed')"),
                ("run_time", "time_ns >= 0"),
            ],
            [("runs_by_account", ["account_id"])],
        ),
        "events": (
            ["account_id", "sequence"],
            [
                ("event_sequence", "sequence > 0"),
                ("event_kind", "kind IN ('opened','intent','accepted','rejected','fill')"),
                (
                    "event_time",
                    "(run_id IS NULL AND time_ns IS NULL AND kind='opened') OR "
                    "(run_id IS NOT NULL AND time_ns IS NOT NULL AND time_ns >= 0 AND kind<>'opened')",
                ),
            ],
            [("events_by_run", ["run_id"])],
        ),
    }
    for table, (keys, checks, indexes) in changes.items():
        with operations.batch_alter_table(table, recreate="always") as batch:
            for key in keys:
                batch.alter_column(key, nullable=False)
            for name, condition in checks:
                batch.create_check_constraint(name, condition)
            for name, columns in indexes:
                batch.create_index(name, columns)
    version_table = operations.create_table(
        "alembic_version", Column("version_num", Text, primary_key=True)
    )
    connection.execute(version_table.insert().values(version_num=revision))
