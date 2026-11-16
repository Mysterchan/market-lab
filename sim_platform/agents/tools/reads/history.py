"""Bounded historical bar slicing, including partial boundary minutes."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Dict, List

from ..protocol.contracts import ToolError
from .formats import bar


def sliced_bars(history: Dict[str, Any], start: int, end: int) -> List[Dict[str, Any]]:
    """Bars in [start, end), recomputing a cut minute from retained trades."""
    cached = history['bars']
    if cached and not history['bars_complete'] and start < cached[0].start_ns:
        raise ToolError('DATA_UNAVAILABLE', 'requested bars are outside the retained window')
    values = []
    for value in cached:
        if value.start_ns < start or value.start_ns >= end:
            continue
        left, right = max(start, value.start_ns), min(end, value.end_ns)
        if right < min(value.end_ns, value.observed_through_ns):
            if not history['trades_complete'] and left < history['trade_start_ns']:
                raise ToolError('DATA_UNAVAILABLE', 'partial minute trades are not retained')
            trades = [
                t
                for t in history['trades']
                if left <= t.timestamp_ns <= right and t.timestamp_ns < value.end_ns
            ]
            if not trades:
                continue
            value = replace(
                value,
                open=trades[0].price,
                high=max(t.price for t in trades),
                low=min(t.price for t in trades),
                close=trades[-1].price,
                volume=sum(t.quantity for t in trades),
                count=len(trades),
                notional=sum(t.price * t.quantity for t in trades),
            )
        values.append(bar(value, end))
    return values
