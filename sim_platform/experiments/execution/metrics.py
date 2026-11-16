"""Constant-space run measurements; thresholds never imply universal deadlines."""

from __future__ import annotations

from time import perf_counter_ns
from typing import Any, Dict


class RuntimeMetrics:
    """Worst market delay and operation time seen during one run.

    The ExperimentSession owns one; only its execution owner thread updates it.
    """

    def __init__(self, threshold_ns: int) -> None:
        self.threshold_ns = threshold_ns
        self.frames = self.turns = 0
        self.maximum_market_delay_ns = self.maximum_operation_ns = 0
        self.measurement_ns = 0

    def frame(self, target_ns: int, event_ns: int) -> None:
        start = perf_counter_ns()
        self.frames += 1
        self.maximum_market_delay_ns = max(
            self.maximum_market_delay_ns, max(0, target_ns - event_ns)
        )
        self.measurement_ns += perf_counter_ns() - start

    def operation(self, duration: int) -> None:
        self.maximum_operation_ns = max(self.maximum_operation_ns, duration)

    def span(self, target_ns: int, first_event_ns: int, count: int) -> None:
        """Conservative delay: charge every logical event the oldest event's lag."""
        start = perf_counter_ns()
        self.frames += count
        self.maximum_market_delay_ns = max(
            self.maximum_market_delay_ns, max(0, target_ns - first_event_ns)
        )
        self.measurement_ns += perf_counter_ns() - start

    def snapshot(self, state: str) -> Dict[str, Any]:
        return dict(
            frames=self.frames,
            turns=self.turns,
            maximum_market_delay_ns=self.maximum_market_delay_ns,
            maximum_operation_ns=self.maximum_operation_ns,
            measurement_ns=self.measurement_ns,
            threshold_ns=self.threshold_ns,
            within_measured_threshold=max(self.maximum_market_delay_ns, self.maximum_operation_ns)
            <= self.threshold_ns,
            platform_ok=state not in ('failed',),
            ranking_eligible=False,
            support_scope='trusted-local-prototype',
        )
