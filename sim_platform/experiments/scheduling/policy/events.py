"""Bounded busy-event summaries, never a substitute for market/order state."""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from sim_platform.domain.json_values import copy_json

# A notification: 'kind' ('market_update' or 'order_update') and optionally
# 'symbol' and 'order_id'.
WorkEvent = Dict[str, Any]
# Wire position: 'time_ns', 'sequence', 'boundary' and 'ordinal' as decimal text.
WirePosition = Dict[str, str]


class EventBuckets:
    """Counts of distinct pending events since the last work, with first/last positions.

    The WorkSchedule owns one and empties it with take() each time work starts.
    Events beyond ``capacity`` distinct keys are only counted as overflow.
    """

    def __init__(self, capacity: int) -> None:
        self.capacity = capacity
        self.values: Dict[Tuple[Any, Any, Any], Dict[str, Any]] = {}
        self.overflow = 0
        self.overflow_first: Optional[WirePosition] = None
        self.overflow_last: Optional[WirePosition] = None

    def add(self, event: WorkEvent, position: WirePosition) -> None:
        key = event['kind'], event.get('symbol'), event.get('order_id')
        if key in self.values:
            value = self.values[key]
            value['last'] = copy_json(position)
            value['count'] += 1
        elif len(self.values) < self.capacity:
            self.values[key] = dict(
                event, first=copy_json(position), last=copy_json(position), count=1
            )
        else:
            self.overflow += 1
            if self.overflow_first is None:
                self.overflow_first = copy_json(position)
            self.overflow_last = copy_json(position)

    def take(self) -> Dict[str, Any]:
        """Return the summary ('events', 'overflow', 'overflow_first', 'overflow_last') and reset."""
        result = dict(
            events=list(self.values.values()),
            overflow=self.overflow,
            overflow_first=self.overflow_first,
            overflow_last=self.overflow_last,
        )
        self.values = {}
        self.overflow = 0
        self.overflow_first = self.overflow_last = None
        return copy_json(result)

    def __bool__(self) -> bool:
        return bool(self.values or self.overflow)
