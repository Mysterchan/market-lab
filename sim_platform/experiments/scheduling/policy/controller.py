"""Agent work policy adapter for the generic ExperimentRuntime process.

No market, file, network, SQL or credentials. Trusted observations are detached
market versions; work delivery is deliberately not a financial source position.
Admission, resync and participant controls are supplied by the lifecycle adapter.
"""

from __future__ import annotations

from concurrent.futures import Future
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Union, cast

from sim_platform.experiments.scheduling.clock import real_delay_ns
from sim_platform.domain.json_values import copy_json
from sim_platform.domain.models import ContractError
from sim_platform.experiments.scheduling.policy.schedule import Delivery, WorkSchedule
from sim_platform.experiments.scheduling.errors import WorkError
from sim_platform.experiments.scheduling.policy.attempts import AttemptPolicy
from sim_platform.experiments.scheduling.policy.budget import ModelBudget

if TYPE_CHECKING:  # Annotation only.
    from sim_platform.experiments.scheduling.clock import ClockReader
    from sim_platform.experiments.scheduling.worker import NoticeCallback

# Most notifications in one observe/resync batch.
MAX_NOTIFICATION_BATCH = 16
# Most admitted tool operations in flight at once.
MAX_INFLIGHT_OPERATIONS = 1024
NS_PER_SECOND = 1e9


class LiveWorkPolicy:
    """The Runtime child's work policy: schedules Agent work and model attempts.

    Built in the child by run_runtime's factory and driven only by its loop thread
    through step() and dispatch(). It owns a WorkSchedule and an AttemptPolicy.

    Args:
        config: 'schedule', 'symbol', 'event_buckets', 'timeout_seconds', 'retry',
            'end_ns' and optionally 'minimum_interval_ns' and 'paced' (turn-based: the
            snapshot also reports 'stepped_ns', the clock time this policy last fully
            stepped at, and 'next_ns', the next time it needs, for the TurnPacer) and
            'budget' (see ModelBudget).
        clock: Experiment clock reader.
        notice: Publishes Runtime notices.
    """

    # Set in __init__; declared here because _delivery is defined first.
    _last_work_ns: Optional[int]
    _last_delivery: Optional[Delivery]

    def _delivery(self, result: Delivery, due: Optional[int]) -> Delivery:
        value = dict(
            work_id=result['work_id'],
            epoch=result['epoch'],
            reason=result['reason'],
            notifications=result['notifications'],
            scheduled_due_ns=str(int(result['position']['time_ns']) if due is None else due),
            dispatched_ns=result['position']['time_ns'],
        )
        self._last_work_ns = int(value['dispatched_ns'])
        self._last_delivery = value
        return value

    def __init__(self, config: Dict[str, Any], clock: ClockReader, notice: NoticeCallback) -> None:
        self.clock, self.notice = clock, notice
        self.work = WorkSchedule(config['schedule'], config['symbol'], config['event_buckets'])
        self.work.delivery_factory = self._delivery
        self.attempts = AttemptPolicy(clock, config['timeout_seconds'], config['retry'])
        self.end_ns = config['end_ns']
        self.observed: Dict[str, Any] = dict(sequence=0, orders={})
        self._events = 0
        self._inflight: Dict[str, int] = {}
        self._stopped = False
        self._paused = False
        self._control_version = 0
        self._control: Optional[Dict[str, Any]] = None
        self._last_delivery = None
        self._last_work_ns = None
        self.minimum_interval_ns = config.get('minimum_interval_ns', 0)
        self.paced = bool(config.get('paced', False))
        self._stepped_ns: Optional[int] = None
        # Clock time a subclass observed the market at during the current step.
        self._market_now: Optional[int] = None
        self.budget = ModelBudget(config.get('budget'))

    def snapshot(self) -> Dict[str, Any]:
        value = dict(
            work=self.work.snapshot(),
            attempt=self.attempts.snapshot(),
            target_ns=self.clock.target_ns(),
            event_sequence=self._events,
            control=self._control,
        )
        if self.paced:
            value.update(stepped_ns=self._stepped_ns, next_ns=self._next_ns())
        return value

    def _next_ns(self) -> Optional[int]:
        """The earliest future time this policy acts at by itself (turn-based pacing)."""
        attempt = self.attempts.snapshot()
        times: List[int] = [self.end_ns]
        if self.work.state == 'idle' and self.work.pending and self._last_work_ns is not None:
            times.append(self._last_work_ns + self.minimum_interval_ns)
        times.extend(
            int(value)
            for value in (self.work.due, attempt['deadline_ns'], attempt['retry_due_ns'])
            if value is not None
        )
        return min(times)

    def _position(self) -> Dict[str, str]:
        return dict(
            time_ns=str(self.clock.target_ns()),
            sequence=str(self.observed['sequence']),
            boundary='0',
            ordinal='0',
        )

    def _notice(self, event: Optional[Dict[str, Any]]) -> None:
        if event is not None:
            if event['event'] == 'attempt_completed':
                self.budget.add((event.get('result') or {}).get('usage'))
            self.notice(event['event'], event.get('details', event))

    def end(self, reason: str) -> None:
        if not self._stopped:
            self._stopped = True
            self.work.end(reason)
            self.attempts.end()
            self.notice('runtime_ended', dict(reason=reason))

    def step(self) -> None:
        """Advance attempts and start due work; nothing happens while stopped or paused."""
        if self._stopped or self._paused:
            return
        clock = self.clock.snapshot()
        if not clock['running']:
            return
        try:
            self._step(clock)
        finally:
            if self.paced and not self._stopped:
                # Everything due at this time has been started; the market was observed
                # up to the earlier of the two readings.
                observed = self._market_now
                self._stepped_ns = (
                    clock['target_ns'] if observed is None else min(observed, clock['target_ns'])
                )
                self._market_now = None

    def _step(self, clock: Dict[str, Any]) -> None:
        if clock['target_ns'] >= self.end_ns:
            self.end('configured session end')
            return
        outcome = self.attempts.step(clock['target_ns'])
        self._notice(outcome)
        if outcome is not None and outcome['event'] == 'attempt_failed':
            self.work.end(outcome['error'], failed=True)
        if (
            self._last_work_ns is not None
            and self.work.state == 'idle'
            and clock['target_ns'] < self._last_work_ns + self.minimum_interval_ns
        ):
            return
        event = self.work.step(self._position(), self.observed)
        if event is not None:
            if event['event'] == 'work_started':
                event['details'].update(self._last_delivery)
            self._notice(event)

    def _next_work(self) -> Union[Delivery, Future[Any]]:
        result = self.work.next_work()
        if isinstance(result, dict):
            return result
        return result.future

    def _observe(self, data: Dict[str, Any], *, resync: bool = False) -> Dict[str, bool]:
        """Apply the next market notification batch (any later one when resyncing)."""
        index = data['event_sequence']
        if type(index) is not int or index < 1:
            raise ContractError('invalid market notification sequence')
        if index <= self._events:
            return dict(duplicate=True)
        if not resync and index != self._events + 1:
            raise WorkError('NOTIFICATION_GAP', 'market notification gap; baseline resync required')
        observation = copy_json(data['observation'])
        if (
            set(observation) != {'sequence', 'orders'}
            or type(observation['sequence']) is not int
            or observation['sequence'] < self.observed['sequence']
            or type(observation['orders']) is not dict
            or any(type(v) is not int or v < 0 for v in observation['orders'].values())
        ):
            raise ContractError('invalid market observation')
        events = copy_json(data['events'])
        if type(events) is not list or len(events) > MAX_NOTIFICATION_BATCH:
            raise ContractError('invalid market notification batch')
        for event in events:
            if (
                type(event) is not dict
                or event.get('kind') not in ('market_update', 'order_update')
                or set(event) - {'kind', 'symbol', 'order_id'}
            ):
                raise ContractError('invalid market event fields')
        self.observed, self._events = observation, index
        for event in events:
            self.work.observe(event, self._position())
        return dict(duplicate=False)

    def _finish_or_wait(self, name: str, data: Dict[str, Any]) -> Dict[str, Any]:
        self.work.validate_identity(data)
        if self._inflight or self.attempts.state in ('running', 'retrying', 'expiry_pending'):
            raise WorkError('WORK_HAS_PENDING_OPERATIONS', 'drain tools and model attempts first')
        now = self.clock.target_ns()
        if name == 'finish':
            return self.work.finish(now)
        from .conditions import validate_wait_conditions

        validate_wait_conditions(data['conditions'], self.work.symbol)
        return self.work.wait(data['request_id'], data['conditions'], now, self.observed)

    def _apply_control(self, data: Dict[str, Any]) -> Dict[str, Any]:
        if (
            type(data['control_version']) is not int
            or data['control_version'] < 1
            or data['action'] not in ('play', 'pause', 'resume', 'stop')
        ):
            raise ContractError('invalid runtime control')
        if data['control_version'] == self._control_version:
            # Versions start at 1, so a matching version means a control was recorded.
            if cast(Dict[str, Any], self._control)['request'] != data:
                raise ContractError('control identity reused with different content')
            return cast(Dict[str, Any], self._control)
        if data['control_version'] != self._control_version + 1:
            raise ContractError('control version gap or stale control')
        self._paused = data['action'] == 'pause'
        self._control_version = data['control_version']
        self._control = dict(
            request=copy_json(data),
            participant='runtime',
            phase='quiescent' if self._paused else 'requested',
            effective_ns=str(self.clock.target_ns()),
            error=None,
        )
        if data['action'] == 'stop':
            self.end('control stop')
            self._control['phase'] = 'terminal'
        return copy_json(self._control)

    def dispatch(self, name: str, data: Dict[str, Any]) -> Any:
        """Handle one validated Runtime command; a Future result is replied to later."""
        if name == 'control':
            return self._apply_control(data)
        if self._stopped:
            # Agent teardown can close its work pipe after the session ends.
            # That notification must not replace the terminal work outcome.
            if name == 'agent_failed':
                return dict(failed=False)
            raise ContractError('runtime has ended')
        if self._paused and name in ('admit', 'begin_attempt', 'finish', 'wait'):
            raise WorkError('PAUSED', 'runtime admission is paused')
        if name == 'inspect':
            return self.snapshot()
        if name == 'next_work':
            return self._next_work()
        if name in ('finish', 'wait'):
            return self._finish_or_wait(name, data)
        if name in ('observe', 'resync'):
            return self._observe(data, resync=name == 'resync')
        if name == 'admit':
            self.work.validate_identity(data)
            if sum(self._inflight.values()) >= MAX_INFLIGHT_OPERATIONS:
                raise WorkError('QUEUE_FULL', 'runtime operation capacity exhausted')
            identity = data['request_id']
            self._inflight[identity] = self._inflight.get(identity, 0) + 1
            return dict(admitted=True)
        if name == 'deliver':
            identity = data['request_id']
            count = self._inflight.get(identity, 1) - 1
            if count:
                self._inflight[identity] = count
            else:
                self._inflight.pop(identity, None)
            return dict(delivered=True)
        if name == 'begin_attempt':
            self.work.validate_identity(data)
            spent = self.budget.exceeded()
            if spent is not None:
                # No model request starts once the budget is spent; the App ends the run.
                if not self.budget.reported:
                    self.budget.reported = True
                    self.notice('budget_exceeded', spent)
                raise WorkError('BUDGET_EXCEEDED', 'the run has spent its model budget')
            event = self.attempts.begin()
            self._notice(event)
            return event
        if name == 'receive_attempt':
            return dict(accepted=self.attempts.receive(**data))
        if name == 'agent_failed':
            if self.work.state != 'failed':
                self.work.end(data['reason'], failed=True)
            self.attempts.end()
            self.notice('agent_failed', dict(reason=data['reason']))
            return dict(failed=True)
        raise ContractError('unsupported work command')

    def wait_seconds(self) -> Optional[float]:
        """Seconds until the next deadline, or None to wait for an input or clock change."""
        if not self.clock.snapshot()['running'] or self._stopped or self._paused:
            return None
        attempt = self.attempts.snapshot()
        deadlines: List[int] = [self.end_ns]
        if self.work.state == 'idle' and self.work.pending and self._last_work_ns is not None:
            deadlines.append(self._last_work_ns + self.minimum_interval_ns)
        deadlines.extend(
            value
            for value in (self.work.due, attempt['deadline_ns'], attempt['retry_due_ns'])
            if value is not None
        )
        return real_delay_ns(self.clock, min(deadlines)) / NS_PER_SECOND
