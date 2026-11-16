"""Canonical finite JSON shared by ORM persistence and public value boundaries."""

import json
from typing import Any

from sim_platform.domain.models import ContractError


def encode(value: Any) -> str:
    """Serialize to canonical JSON text: sorted keys, no spaces, no NaN or Infinity."""
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ContractError("value must be finite JSON data") from exc


def copy_json(value: Any) -> Any:
    """Return a detached deep copy, rejecting anything that is not finite JSON data."""
    return json.loads(encode(value))
