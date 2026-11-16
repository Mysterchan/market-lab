"""Repository SQL operations; returns detached domain values."""

from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING, Any, ClassVar, Dict, Optional, Tuple, Type, Union, cast

from sqlalchemy.orm import Session
from sim_platform.domain.models import ContractError
from sim_platform.persistence.sql.mapping import to_domain as _domain

if TYPE_CHECKING:  # Annotation only.
    from sim_platform.persistence.sql.models import Base

# Rows fetched per batch when a query result is streamed.
STREAM_BATCH_ROWS = 256

# A primary key: one text ID, or a tuple for composite keys.
RowKey = Union[str, Tuple[Any, ...]]


class Repository:
    """SQL access for one table inside the caller's Session (one unit of work).

    Repositories creates one per Session; it caches rows it has read so repeated
    lookups in the same transaction return the same ORM object. Subclasses set
    ``record_type`` (ORM class) and ``model_type`` (domain dataclass).
    """

    record_type: ClassVar[Optional[Type[Base]]] = None
    model_type: ClassVar[Optional[Type[Any]]] = None

    def __init__(self, session: Session) -> None:
        self.session = session
        self._rows: Dict[Tuple[Any, RowKey], Any] = {}

    def row(self, record_type: Type[Base], key: RowKey) -> Any:
        """The ORM row for ``key`` (None if missing), cached for this Session."""
        identity = (record_type, key)
        if identity not in self._rows or self._rows[identity] is None:
            self._rows[identity] = self.session.get(record_type, key)
        return self._rows[identity]

    def get(self, key: RowKey) -> Any:
        """The detached ``model_type`` value for ``key``; raise if it does not exist.

        Typed Any because the class is not generic: the value is always an instance
        of the subclass's ``model_type`` (for example Run for RunRepository).
        """
        record_type = cast('Type[Base]', self.record_type)
        row = self.row(record_type, key)
        if row is None:
            raise ContractError(f"unknown {record_type.__tablename__} ID")
        return _domain(row, cast(Type[Any], self.model_type))

    def add(self, model: Any) -> None:
        """Insert a domain dataclass value as a new row and flush it."""
        self.session.add(cast('Type[Base]', self.record_type)(**asdict(model)))
        self.session.flush()
