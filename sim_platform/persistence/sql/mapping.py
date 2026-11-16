"""Detach ORM records into independent domain values."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Type, TypeVar

from sim_platform.domain.json_values import copy_json

if TYPE_CHECKING:  # Annotation only.
    from _typeshed import DataclassInstance

    T = TypeVar('T', bound=DataclassInstance)


def to_domain(record: Any, model: Type[T]) -> T:
    """Copy the ORM row's fields named by dataclass ``model`` into a new ``model``."""
    return model(**{name: copy_json(getattr(record, name)) for name in model.__dataclass_fields__})
