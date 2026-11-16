"""Owner-only single-work scheduling over effective market time.

No threads, provider calls, storage or live market dependencies. Observations
are supplied by the application adapter. Future callbacks belong only to the
internal ordered output stage, never to user code on the owner.
"""

from __future__ import annotations

from concurrent.futures import Future
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional, Union

from sim_platform.domain.json_values import copy_json
from sim_platform.experiments.scheduling.errors import WorkError
from sim_platform.experiments.execution.results import DeferredResult
from sim_platform.experiments.scheduling.policy.events import (
    EventBuckets,
    WirePosition,
    WorkEvent,
)

NS_PER_SECOND = 1_000_000_000

# A work delivery: 'work_id', 'epoch', 'position', 'reason' and 'notifications'
# (reshaped by delivery_factory before the Agent receives it).
Delivery = Dict[str, Any]
# An event for the Runtime log: 'event' and 'details'.
ScheduleEvent = Dict[str, Any]
# Market and own-order versions: 'sequence' (int) and 'orders' (order ID to version).
Observation = Dict[str, Any]


def seconds_ns(value: Any) -> int:
    """Convert decimal seconds (text or number) to whole nanoseconds."""
    return int(Decimal(value) * NS_PER_SECOND)


class WorkSchedule:
    """When the single Agent work item starts, waits and finishes, on experiment time.

    The LiveWorkPolicy owns one inside the Runtime child; only its loop thread uses
    it. ``epoch`` changes when work ends, so stale requests are refused.

    Args:
        schedule: Configured 'mode' ('event', 'interval' or 'continuous'), 'events',
            'busy_event_policy' and, for intervals, 'interval_seconds'.
        symbol: The experiment's symbol.
        bucket_capacity: Most distinct pending events kept between work items.
    """

    def __init__(self, schedule: Dict[str, Any], symbol: str, bucket_capacity: int) -> None:
        self.schedule = copy_json(schedule)
        self.symbol = symbol
        self.pending = EventBuckets(bucket_capacity)
        self.state = 'idle'
        self.number = 0
        self.epoch = 1
        self.due: Optional[int] = None
        self.conditions: List[Dict[str, Any]] = []
        self.wait_id: Optional[str] = None
        self.started = False
        self._delivery: Optional[Delivery] = None
        self._waiter: Optional[Future[Any]] = None
        self.failure: Optional[str] = None
        self.delivery_factory: Optional[Callable[[Delivery, Optional[int]], Delivery]] = None

    @property
    def identity(self) -> Dict[str, str]:
        return dict(work_id='work-' + str(max(1, self.number)), epoch=str(self.epoch))

    @property
    def needs_orders(self) -> bool:
        return any(e['kind'] == 'order_update' for e in self.schedule['events']) or any(
            c['kind'] == 'order_changed' for c in self.conditions
        )

    def _configured(self, event: WorkEvent) -> bool:
        return any(
            all(event.get(key) == value for key, value in spec.items())
            for spec in self.schedule['events']
        )

    def observe(self, event: WorkEvent, position: WirePosition) -> None:
        if self.state not in ('failed', 'stopped') and self._configured(event):
            self.pending.add(event, position)

    def _wake(self, position: WirePosition, reason: str) -> ScheduleEvent:
        due = self.due
        self.number += 1
        self.state = 'running'
        self.due = None
        self.conditions = []
        self.wait_id = None
        self._delivery = dict(
            self.identity,
            position=copy_json(position),
            reason=reason,
            notifications=self.pending.take(),
        )
        if self.delivery_factory is not None:
            self._delivery = self.delivery_factory(self._delivery, due)
        if self._waiter is not None:
            self._waiter.set_result(self._delivery)
            self._delivery = None
            self._waiter = None
        return dict(event='work_started', details=dict(self.identity, reason=reason))

    def _condition_met(self, observation: Observation) -> bool:
        return any(
            (c['kind'] == 'market_changed' and observation['sequence'] > int(c['after_sequence']))
            or (
                c['kind'] == 'order_changed'
                and observation['orders'].get(c['order_id'], 0) > int(c['after_version'])
            )
            for c in self.conditions
        )

    def step(self, position: WirePosition, observation: Observation) -> Optional[ScheduleEvent]:
        """Start work if it is due or a wait/notification condition is met."""
        if self.state in ('failed', 'stopped', 'running'):
            return None
        now = int(position['time_ns'])
        if not self.started:
            self.started = True
            if self.schedule['mode'] != 'event':
                return self._wake(position, 'initial')
        if self.conditions:
            ready = (self.due is not None and now >= self.due) or self._condition_met(observation)
        else:
            ready = (self.due is not None and now >= self.due) or (
                bool(self.pending)
                and (
                    self.schedule['mode'] == 'event'
                    or self.schedule['busy_event_policy'] == 'wake_on_pending_event'
                )
            )
        return self._wake(position, 'wait' if self.conditions else 'schedule') if ready else None

    def validate_identity(self, request: Dict[str, Any]) -> None:
        if (request['work_id'], request['epoch']) != (
            self.identity['work_id'],
            self.identity['epoch'],
        ):
            raise WorkError('STALE_WORK', 'request belongs to an inactive work')
        if self.state != 'running':
            raise WorkError('STALE_WORK', 'work is not running')

    def next_work(self) -> Union[Delivery, DeferredResult]:
        """The ready delivery, or a DeferredResult that resolves when work next starts."""
        if self._delivery is not None:
            result, self._delivery = self._delivery, None
            return result
        if self.state == 'running':
            raise WorkError(
                'WORK_HAS_PENDING_OPERATIONS', 'finish current work before requesting another'
            )
        if self.state in ('failed', 'stopped'):
            raise WorkError('AGENT_CRASH', self.failure or 'agent work stopped')
        if self._waiter is not None:
            raise WorkError('QUEUE_FULL', 'one work subscription is already pending')
        self._waiter = Future()
        return DeferredResult(self._waiter)

    def finish_data(self, now: int) -> Dict[str, Any]:
        mode = self.schedule['mode']
        due = (
            now
            if mode == 'continuous'
            else now + seconds_ns(self.schedule['interval_seconds']) if mode == 'interval' else None
        )
        return dict(work_state='idle', next_due_ns=str(due) if due is not None else None)

    def finish(self, now: int) -> Dict[str, Any]:
        data = self.finish_data(now)
        self._delivery = None
        self.state = 'idle'
        self.due = int(data['next_due_ns']) if data['next_due_ns'] is not None else None
        return data

    def wait(
        self,
        request_id: str,
        conditions: List[Dict[str, Any]],
        now: int,
        observation: Observation,
    ) -> Dict[str, Any]:
        """Register validated OR conditions against current own-order/market versions."""
        # Validate the whole condition set before replacing the schedule.
        for condition in conditions:
            kind = condition['kind']
            if (
                kind == 'market_changed'
                and int(condition['after_sequence']) > observation['sequence']
            ):
                raise WorkError('INVALID_ARGUMENT', 'wait sequence exceeds observed market')
            if kind == 'order_changed':
                version = observation['orders'].get(condition['order_id'])
                if version is None:
                    raise WorkError('ORDER_NOT_FOUND', 'wait order is not in this run')
                if int(condition['after_version']) > version:
                    raise WorkError('INVALID_ARGUMENT', 'wait version exceeds observed order')
        deadlines = [now + seconds_ns(c['seconds']) for c in conditions if c['kind'] == 'after']
        self._delivery = None
        self.conditions = copy_json(conditions)
        self.due = min(deadlines) if deadlines else None
        self.wait_id = 'wait-' + str(self.number)
        self.state = 'waiting'
        return dict(work_state='waiting', wait_id=self.wait_id)

    def end(self, reason: str, failed: bool = False) -> None:
        self.state = 'failed' if failed else 'stopped'
        self.failure = reason
        self.epoch += 1
        self.due = None
        self.conditions = []
        self._delivery = None
        if self._waiter is not None:
            self._waiter.cancel()
            self._waiter = None

    def snapshot(self) -> Dict[str, Any]:
        return dict(
            self.identity,
            state=self.state,
            due_ns=self.due,
            wait_id=self.wait_id,
            failure=self.failure,
        )
