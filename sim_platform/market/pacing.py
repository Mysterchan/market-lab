"""Wall-clock pacing for synchronous playback; the caller owns advancement.

This stores no market state and starts no thread. Policy runners and bare market
playback each own a schedule; background agent scheduling remains in its pipeline.
"""

from __future__ import annotations

from math import isfinite
from time import monotonic
from typing import Any, Callable, Optional, Tuple

from sim_platform.domain.models import ContractError

SECOND_NS = 1_000_000_000


def validate_poll(max_events: int, budget_seconds: Optional[float]) -> None:
    """Reject a non-positive event count or a non-positive, non-finite time budget."""
    if type(max_events) is not int or max_events < 1:
        raise ContractError('max_events must be a positive integer')
    if budget_seconds is not None and (
        type(budget_seconds) not in (int, float)
        or not isfinite(budget_seconds)
        or budget_seconds <= 0
    ):
        raise ContractError('budget_seconds must be positive and finite')


class PlaybackSchedule:
    """Decide how many market events are due, given real elapsed time and a speed.

    The schedule only answers "is the next event due yet"; the caller's ``pump``
    function performs the advance. ``speed=None`` means unpaced: every poll advances.
    """

    def __init__(self, market: Any) -> None:
        self._market = market
        # (wall-clock start, market time at start, speed) while paced playback runs.
        self._origin: Optional[Tuple[float, int, float]] = None

    def play(self, speed: Optional[float] = None) -> None:
        """Start pacing from the market's current time; None plays without pacing."""
        if speed is not None and (
            type(speed) not in (int, float) or not isfinite(speed) or speed <= 0
        ):
            raise ContractError('playback speed must be positive and finite')
        self._origin = (monotonic(), self._market.time_ns, speed) if speed is not None else None

    def pause(self) -> None:
        self._origin = None

    def poll(
        self,
        pump: Callable[[int], Any],
        *,
        max_events: int = 1,
        budget_seconds: Optional[float] = None,
    ) -> None:
        """Advance events that are due, up to ``max_events`` or the time budget.

        Stops early when the next event is not yet due, when ``pump`` reports nothing
        advanced, or when ``budget_seconds`` of real time has been used.
        """
        validate_poll(max_events, budget_seconds)
        deadline = monotonic() + budget_seconds if budget_seconds is not None else None
        for _ in range(max_events):
            if self._origin is not None:
                wall, simulation, speed = self._origin
                due = simulation + int((monotonic() - wall) * speed * SECOND_NS)
                next_time = self._market.next_event_time_ns
                if next_time is not None and next_time > due:
                    break
            if not pump(1):
                break
            if deadline is not None and monotonic() >= deadline:
                break
