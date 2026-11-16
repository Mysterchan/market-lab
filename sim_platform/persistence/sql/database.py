"""Connection lifecycle, SQLite transaction semantics, and ORM units of work."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional, Union

from sqlalchemy import URL, create_engine, event
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool, QueuePool

from sim_platform.domain.models import ContractError
from sim_platform.persistence.paths import data_directory
from sim_platform.persistence.sql.migrations import initialize


# How long SQLite waits for another writer's lock before failing, in milliseconds.
BUSY_TIMEOUT_MS = 5000


def _engine(path: Path, *, migration: bool = False) -> Engine:
    """SQLite engine with explicit BEGIN; migration engines disable foreign keys and pooling."""
    engine = create_engine(
        URL.create("sqlite", database=str(path)),
        poolclass=NullPool if migration else QueuePool,
        connect_args={'check_same_thread': False},
    )

    @event.listens_for(engine, "connect")
    def configure(dbapi_connection: Any, connection_record: Any) -> None:
        dbapi_connection.isolation_level = None
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = OFF" if migration else "PRAGMA foreign_keys = ON")
        cursor.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
        cursor.execute("PRAGMA synchronous = FULL")
        cursor.close()

    @event.listens_for(engine, "begin")
    def begin(connection: Connection) -> None:
        # Real BEGIN for reads provides consistent multi-query snapshots too.
        write = migration or connection.get_execution_options().get("write", False)
        connection.exec_driver_sql("BEGIN IMMEDIATE" if write else "BEGIN")

    return engine


class Database:
    """The platform SQLite database: migrated on open, then used through units of work.

    Platform creates one per data directory and closes it last; close() disposes the
    connection pool. Each unit_of_work() is one short transaction.
    """

    def __init__(self, directory: Optional[Union[Path, str]] = None) -> None:
        self.directory = Path(directory).expanduser() if directory is not None else data_directory()
        if not self.directory.is_absolute():
            raise ContractError("data directory must be absolute")
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "platform.sqlite"
        bootstrap = _engine(self.path, migration=True)
        try:
            with bootstrap.begin() as connection:
                initialize(connection)
        finally:
            bootstrap.dispose()
        self.engine = _engine(self.path)
        self._closed = False

    @contextmanager
    def unit_of_work(self, *, write: bool = False) -> Iterator[Session]:
        """Short-lived ORM Session: one operation, one transaction, no shared state."""
        if self._closed:
            raise ContractError("platform is closed")
        with self.engine.connect().execution_options(write=write) as connection:
            with Session(bind=connection, expire_on_commit=False) as session:
                with session.begin():
                    yield session

    def close(self) -> None:
        self.engine.dispose()
        # dispose() closes pooled connections; a connection still referenced by an
        # abandoned session closes when collected. Collect now so the database file is
        # released at close (Windows cannot delete a file that is still open).
        import gc

        gc.collect()
        self._closed = True
