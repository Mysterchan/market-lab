"""Ordered SQLite schema revisions, executed atomically by the startup runner."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import inspect, insert, select

from sim_platform.domain.models import ContractError
from sim_platform.persistence.sql.models import Base, SchemaRevisionRecord
from sim_platform.persistence.sql.migrations import (
    v0002_orm,
    v0003_execution,
    v0004_incremental,
    v0005_projections,
    v0006_runtime,
    v0007_agents,
)

if TYPE_CHECKING:  # Annotation only.
    from sqlalchemy.engine import Connection

# SQLite application_id marking a database as this platform's ('TSM1').
APPLICATION_ID = 0x54534D31
SCHEMA_VERSION = 7
REVISION = v0007_agents.revision


def _validate_layout(connection: Connection, version: int) -> None:
    """Raise unless the tables and columns match schema ``version`` exactly."""
    inspector = inspect(connection)
    expected = {
        table.name: table
        for table in Base.metadata.sorted_tables
        if (version >= 7 or table.name not in ('agent_revisions', 'agent_experiments'))
        and (version >= 6 or table.name != 'run_checkpoints')
        and (version >= 5 or table.name not in ('ledger_items', 'research_archives'))
        and (version >= 4 or table.name not in ('account_checkpoints', 'decision_records'))
        and (version != 1 or table.name != 'alembic_version')
    }
    if set(inspector.get_table_names()) != set(expected):
        raise ContractError("database table layout does not match its schema version")
    for name, table in expected.items():
        actual = {column["name"] for column in inspector.get_columns(name)}
        if actual != set(table.columns.keys()):
            raise ContractError(f"unexpected columns in {name}")


def initialize(connection: Connection) -> None:
    """Fresh creation or supported upgrade in the caller's BEGIN IMMEDIATE.

    Connection must have SQLite foreign_keys disabled before BEGIN so Alembic can
    rebuild referenced tables. Validate referential integrity before committing;
    normal application connections always enable foreign_keys.
    """
    version = connection.exec_driver_sql("PRAGMA user_version").scalar_one()
    app_id = connection.exec_driver_sql("PRAGMA application_id").scalar_one()
    tables = inspect(connection).get_table_names()
    if version == 0 and app_id == 0 and not tables:
        Base.metadata.create_all(connection)
        v0007_agents.install_triggers(connection)
        connection.execute(insert(SchemaRevisionRecord).values(version_num=REVISION))
    elif app_id != APPLICATION_ID or version not in (1, 2, 3, 4, 5, 6, SCHEMA_VERSION):
        raise ContractError("unsupported or foreign database; no migration performed")
    elif version == 1:
        _validate_layout(connection, 1)
        v0002_orm.upgrade(connection)
        v0003_execution.upgrade(connection)
        v0004_incremental.upgrade(connection)
        v0005_projections.upgrade(connection)
        v0006_runtime.upgrade(connection)
    else:
        _validate_layout(connection, version)
        revisions = connection.execute(select(SchemaRevisionRecord.version_num)).scalars().all()
        expected_revision = {
            2: v0002_orm.revision,
            3: v0003_execution.revision,
            4: v0004_incremental.revision,
            5: v0005_projections.revision,
            6: v0006_runtime.revision,
            7: REVISION,
        }[version]
        if revisions != [expected_revision]:
            raise ContractError("database revision does not match its schema version")
        if version == 2:
            v0003_execution.upgrade(connection)
        if version < 4:
            v0004_incremental.upgrade(connection)
        if version < 5:
            v0005_projections.upgrade(connection)
        if version < 6:
            v0006_runtime.upgrade(connection)
    if 0 < version < 7:
        v0007_agents.upgrade(connection)
    v0007_agents.validate_triggers(connection)
    if connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall():
        raise ContractError("database contains invalid foreign key references")
    connection.exec_driver_sql(f"PRAGMA application_id = {APPLICATION_ID}")
    connection.exec_driver_sql(f"PRAGMA user_version = {SCHEMA_VERSION}")
