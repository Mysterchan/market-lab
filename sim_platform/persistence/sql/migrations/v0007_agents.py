"""Add immutable Agent definitions and prepared experiments; existing run/ledger/rules bytes remain untouched."""

from __future__ import annotations

from sqlalchemy import text

from typing import TYPE_CHECKING, List, Tuple, cast

if TYPE_CHECKING:  # Annotation only.
    from sqlalchemy.engine import Connection

revision = '0007_agents'
down_revision = '0006_runtime'
TRIGGERS = tuple(
    f'{table}_no_{operation.lower()}'
    for table in ('agent_revisions', 'agent_experiments')
    for operation in ('UPDATE', 'DELETE')
)


def install_triggers(connection: Connection) -> None:
    """Make the Agent tables append-only with UPDATE/DELETE triggers."""
    for table in ('agent_revisions', 'agent_experiments'):
        for operation in ('UPDATE', 'DELETE'):
            name = f'{table}_no_{operation.lower()}'
            connection.exec_driver_sql(
                f"CREATE TRIGGER {name} BEFORE {operation} ON {table} "
                "BEGIN SELECT RAISE(ABORT, 'agent storage is append-only'); END"
            )


def validate_triggers(connection: Connection) -> None:
    """Raise unless every append-only trigger exists with its exact definition."""
    rows = connection.exec_driver_sql(
        "SELECT name, sql FROM sqlite_master WHERE type='trigger'"
    ).fetchall()
    found = dict(cast(List[Tuple[str, str]], rows))
    for table in ('agent_revisions', 'agent_experiments'):
        for operation in ('UPDATE', 'DELETE'):
            name = f'{table}_no_{operation.lower()}'
            expected = f"CREATE TRIGGER {name} BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT, 'agent storage is append-only'); END"
            if found.get(name) != expected:
                from sim_platform.domain.models import ContractError

                raise ContractError('agent append-only trigger does not match schema')


def upgrade(connection: Connection) -> None:
    connection.exec_driver_sql(
        '''CREATE TABLE agent_revisions (
        agent_id TEXT NOT NULL, revision INTEGER NOT NULL, profile_id TEXT NOT NULL,
        definition TEXT NOT NULL,
        PRIMARY KEY (agent_id, revision),
        CONSTRAINT agent_revision_positive CHECK (revision > 0),
        FOREIGN KEY(profile_id) REFERENCES profiles (id))'''
    )
    connection.exec_driver_sql(
        'CREATE INDEX agent_revisions_by_profile ON agent_revisions (profile_id, agent_id)'
    )
    connection.exec_driver_sql(
        '''CREATE TABLE agent_experiments (
        id TEXT NOT NULL PRIMARY KEY, profile_id TEXT NOT NULL, account_id TEXT NOT NULL,
        agent_id TEXT NOT NULL, revision INTEGER NOT NULL, config TEXT NOT NULL,
        initial_snapshot TEXT NOT NULL, execution_config TEXT NOT NULL,
        FOREIGN KEY(profile_id) REFERENCES profiles (id),
        FOREIGN KEY(account_id) REFERENCES accounts (id),
        FOREIGN KEY(agent_id,revision) REFERENCES agent_revisions (agent_id,revision))'''
    )
    connection.exec_driver_sql(
        'CREATE INDEX agent_experiments_by_profile ON agent_experiments (profile_id, id)'
    )
    install_triggers(connection)
    connection.execute(
        text('UPDATE alembic_version SET version_num=:revision'), {'revision': revision}
    )
