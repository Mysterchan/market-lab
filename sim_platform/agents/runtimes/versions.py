"""Explicit CPython request policy; no automatic backward-compatibility guesses."""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from sim_platform.agents.preparation.errors import PreparationError

DEFAULT_PYTHON = '3.12'
SUPPORTED_MINORS = tuple((3, minor) for minor in range(9, 15))
# A version: minor, minor.patch; a range bound adds a comparison operator.
VERSION_PATTERN = r'3\.[0-9]{1,2}(?:\.[0-9]{1,3})?'
BOUND_PATTERN = r'\s*(>=|<=|>|<|==)\s*(3\.[0-9]{1,2}(?:\.[0-9]{1,3})?)\s*'
# Longest accepted request text.
REQUEST_MAX_LENGTH = 100
# Patch numbers probed per minor when deciding which minors a range allows.
PROBE_PATCHES = (0, 999)

Version = Tuple[int, ...]


def version_tuple(value: Any) -> Version:
    """Parse '3.N' or '3.N.P' as (3, N, P), with a missing patch as 0."""
    if not isinstance(value, str) or not re.fullmatch(VERSION_PATTERN, value):
        raise PreparationError(
            'INVALID_PYTHON_VERSION', 'use a CPython minor, patch, or bounded version range'
        )
    parts = tuple(int(item) for item in value.split('.'))
    return parts + (0,) * (3 - len(parts))


class PythonRequest:
    """A project's Python request: a minor, an exact patch, or a list of range bounds.

    ``candidates`` lists the supported minors the request allows. Created wherever a
    request is checked; it holds no resources.
    """

    def __init__(self, text: Any) -> None:
        if not isinstance(text, str) or len(text) > REQUEST_MAX_LENGTH:
            raise PreparationError('INVALID_PYTHON_VERSION', 'invalid Python request')
        self.text = text
        self.minor: Optional[Version] = None
        self.exact: Optional[Version] = None
        self.bounds: List[Tuple[str, Version]] = []
        if re.fullmatch(VERSION_PATTERN, text):
            parts = version_tuple(text)
            self.minor = parts[:2]
            if text.count('.') == 2:
                self.exact = parts
        else:
            for bound in text.split(','):
                match = re.fullmatch(BOUND_PATTERN, bound)
                if match is None:
                    raise PreparationError('INVALID_PYTHON_VERSION', 'invalid Python version range')
                self.bounds.append((match[1], version_tuple(match[2])))
        self.candidates: List[Tuple[int, int]] = []
        for minor in SUPPORTED_MINORS:
            points = set(PROBE_PATCHES)
            points.update(
                value[2] + delta
                for _, value in self.bounds
                if value[:2] == minor
                for delta in (0, 1)
            )
            if self.exact is not None:
                points.add(self.exact[2])
            if any(self.matches('.'.join(map(str, (*minor, point)))) for point in points):
                self.candidates.append(minor)
        if not self.candidates:
            raise PreparationError(
                'PYTHON_UNAVAILABLE', 'request is outside supported CPython 3.9–3.14'
            )

    def matches(self, version: str) -> bool:
        value = version_tuple(version)
        if value[:2] not in SUPPORTED_MINORS or self.minor is not None and value[:2] != self.minor:
            return False
        if self.exact is not None and value != self.exact:
            return False
        checks: Dict[str, Callable[[Version, Version], bool]] = {
            '>=': lambda a, b: a >= b,
            '<=': lambda a, b: a <= b,
            '>': lambda a, b: a > b,
            '<': lambda a, b: a < b,
            '==': lambda a, b: a == b,
        }
        return all(checks[operation](value, bound) for operation, bound in self.bounds)
