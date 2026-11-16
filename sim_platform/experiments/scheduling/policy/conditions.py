"""Wait-condition validation shared by tools and the scheduling policy."""

from __future__ import annotations

import re
from typing import Any

from ..errors import WorkError

# A wait names between 1 and this many OR conditions.
MAX_WAIT_CONDITIONS = 8
# Longest 'seconds' text, and its decimal syntax (up to nanosecond precision).
SECONDS_TEXT_MAX_LENGTH = 24
SECONDS_PATTERN = r'(0|[1-9][0-9]*)(\.[0-9]{1,9})?'
NS_PER_SECOND = 10**9
# Largest signed 64-bit integer, the upper bound for a wait in nanoseconds.
MAX_INT64 = 2**63 - 1
# Most digits in a market sequence or order version counter.
COUNTER_MAX_DIGITS = 20
# Longest order identifier, and its allowed characters.
IDENTIFIER_MAX_LENGTH = 128
IDENTIFIER_PATTERN = r'[A-Za-z0-9][A-Za-z0-9._:-]*'


def validate_wait_conditions(conditions: Any, symbol: Any) -> None:
    """Exact OR condition shape and bounded counters; current versions checked by owner.

    ``symbol`` is the experiment's symbol, or the symbols of a run on several.
    """
    if type(conditions) is not list or not 1 <= len(conditions) <= MAX_WAIT_CONDITIONS:
        raise WorkError('INVALID_ARGUMENT', 'wait requires one to eight conditions')
    fields = {
        'after': {'kind', 'seconds'},
        'market_changed': {'kind', 'symbol', 'after_sequence'},
        'order_changed': {'kind', 'order_id', 'after_version'},
    }
    from decimal import Decimal, InvalidOperation

    for condition in conditions:
        if (
            type(condition) is not dict
            or not isinstance(condition.get('kind'), str)
            or set(condition) != fields.get(condition['kind'], set())
        ):
            raise WorkError('INVALID_ARGUMENT', 'invalid wait condition')
        kind = condition['kind']
        if kind == 'after':
            value = condition['seconds']
            try:
                syntax = (
                    isinstance(value, str)
                    and len(value) <= SECONDS_TEXT_MAX_LENGTH
                    and re.fullmatch(SECONDS_PATTERN, value)
                )
                number = Decimal(value) if syntax else Decimal(0)
                valid = number.is_finite() and 1 <= number * NS_PER_SECOND <= MAX_INT64
            except (InvalidOperation, ValueError, OverflowError):
                valid = False
            if not valid:
                raise WorkError(
                    'INVALID_ARGUMENT', 'wait duration must be positive whole nanoseconds'
                )
        else:
            name = 'after_sequence' if kind == 'market_changed' else 'after_version'
            value = condition[name]
            if (
                not isinstance(value, str)
                or not value.isascii()
                or not value.isdecimal()
                or len(value) > COUNTER_MAX_DIGITS
                or (len(value) > 1 and value.startswith('0'))
            ):
                raise WorkError('INVALID_ARGUMENT', 'invalid wait counter')
            symbols = (symbol,) if isinstance(symbol, str) else tuple(symbol)
            if kind == 'market_changed' and condition['symbol'] not in symbols:
                raise WorkError('INVALID_ARGUMENT', 'wait symbol differs from experiment')
            if kind == 'order_changed':
                value = condition['order_id']
                if (
                    not isinstance(value, str)
                    or len(value) > IDENTIFIER_MAX_LENGTH
                    or re.fullmatch(IDENTIFIER_PATTERN, value) is None
                ):
                    raise WorkError('INVALID_SCHEMA', 'invalid identifier')
