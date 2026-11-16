"""Bounded display history, published by output and borrowed by presentation."""

from __future__ import annotations

from collections import deque
from threading import Lock
import json
from typing import Any, Deque, Dict, List

# Activity items kept for display when no limit is given.
DEFAULT_ACTIVITY_LIMIT = 80
# Longest 'details' text kept for one item; longer text is cut and marked truncated.
DETAIL_TEXT_LIMIT = 8192

# One display item: 'event', 'time_ns', 'status', 'details' (JSON text) and 'truncated'.
ActivityItem = Dict[str, Any]


class RecentActivity:
    """The latest Agent activity, shortened for display; safe across threads.

    The Agent driver creates one per run. The output thread records into it and the
    presentation copies it with snapshot().
    """

    def __init__(self, limit: int = DEFAULT_ACTIVITY_LIMIT) -> None:
        self._items: Deque[ActivityItem] = deque(maxlen=limit)
        self._lock = Lock()

    def record(self, message: Dict[str, Any]) -> None:
        request = message.get('request') or {}
        result = message.get('result') or {}
        position = message.get('position') or {}
        event = message.get('event', request.get('tool', message['kind']))
        detail = (
            dict(request=request, result=result)
            if message.get('kind') == 'result'
            else message.get('details', result)
        )
        details = json.dumps(detail, ensure_ascii=False, default=str)
        item = dict(
            event=event,
            time_ns=position.get('time_ns'),
            status=result.get('status', '') if isinstance(result, dict) else '',
            details=details[:DETAIL_TEXT_LIMIT],
            truncated=len(details) > DETAIL_TEXT_LIMIT,
        )
        with self._lock:
            self._items.append(item)

    def snapshot(self) -> List[ActivityItem]:
        with self._lock:
            return [dict(item) for item in self._items]
