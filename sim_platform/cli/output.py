"""JSON output for complete command results and streaming session events."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
import json
from typing import Any


def emit_json(result: object) -> int:
    """Print an indented JSON result and return the success exit code."""
    print(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False))
    return 0


def emit_event(value: object) -> None:
    """Flush a single JSON line, including nested domain dataclasses."""

    def encode(item: Any) -> Any:
        if is_dataclass(item) and not isinstance(item, type):
            return asdict(item)
        raise TypeError('unsupported output value')

    print(json.dumps(value, default=encode, ensure_ascii=False), flush=True)
