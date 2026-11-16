"""Random identifiers for new saved objects."""

from __future__ import annotations

from uuid import uuid4


def identifier() -> str:
    """A new random ID as 32 lower-case hex digits."""
    return uuid4().hex
