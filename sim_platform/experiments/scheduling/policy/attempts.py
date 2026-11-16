"""Provider-independent deadline/retry policy; Agent supplies provider IO.

Trusted ingress timestamps responses using the effective clock. Owner evaluates
responses before deadline expiry, so delayed servicing cannot reject an on-time
reply. At most one response and one active attempt are retained.
"""

from __future__ import annotations

from threading import RLock
from typing import TYPE_CHECKING, Any, Dict, Optional, cast

from sim_platform.domain.json_values import copy_json, encode
from sim_platform.domain.models import ContractError
from sim_platform.experiments.scheduling.policy.schedule import seconds_ns

if TYPE_CHECKING:  # Annotation only.
    from sim_platform.experiments.scheduling.clock import ClockReader

# Largest encoded model result kept for one attempt (64 KiB).
MAX_RESULT_BYTES = 65_536

# An attempt event: 'event' ('attempt_dispatched', 'attempt_completed',
# 'attempt_retry', 'attempt_failed' or 'attempt_expiry_pending') and its details.
AttemptEvent = Dict[str, Any]

RETRY_CLASS = {
    'TRANSPORT_ERROR': 'transport',
    'RATE_LIMIT': 'rate_limit',
    'PROVIDER_TEMPORARY': 'temporary_provider',
    'MODEL_TIMEOUT': 'timeout',
}
FATAL = frozenset(('AUTH_FAILED', 'CONTEXT_TOO_LONG', 'MODEL_UNSUPPORTED', 'AGENT_CRASH'))


class AttemptPolicy:
    """Deadlines and retries for model attempts; the Agent performs the actual calls.

    The LiveWorkPolicy owns one inside the Runtime child. At most one attempt and
    one received reply exist at a time; a lock guards them against the reply path.

    Args:
        clock: Experiment clock reader.
        timeout_seconds: Per-attempt deadline as decimal seconds.
        retry: 'retry_classes', 'max_attempts', 'format_max_attempts',
            'base_delay_seconds' and 'max_delay_seconds'.
    """

    def __init__(self, clock: ClockReader, timeout_seconds: Any, retry: Dict[str, Any]) -> None:
        self.clock = clock
        self.timeout = seconds_ns(timeout_seconds)
        self.retry = copy_json(retry)
        self._lock = RLock()
        self.identity: Optional[str] = None
        self.deadline: Optional[int] = None
        self.retry_due: Optional[int] = None
        self.count = 0
        self.format_count = 0
        self._reply: Optional[Dict[str, Any]] = None
        self.state = 'idle'
        self.require_receipt_seal = False
        self._receipt_sealed = False

    def _dispatch(self, now: int) -> AttemptEvent:
        self.count += 1
        self.identity = str(self.count)
        self.deadline = now + self.timeout
        self.retry_due = None
        self._reply = None
        self.state = 'running'
        self._receipt_sealed = False
        return dict(
            event='attempt_dispatched',
            attempt_id=self.identity,
            dispatched_ns=str(now),
            deadline_ns=str(self.deadline),
        )

    def begin(self) -> AttemptEvent:
        with self._lock:
            if self.state in ('running', 'retrying', 'expiry_pending'):
                raise ContractError('model attempt already active')
            # Attempt IDs remain unique across requests/work; budgets reset separately.
            self._first = self.count
            self.format_count = 0
            return self._dispatch(self.clock.target_ns())

    def receive_at(
        self,
        attempt_id: str,
        received_ns: Any,
        *,
        result: Any = None,
        error: Optional[str] = None,
        retry_after_seconds: Optional[str] = None,
    ) -> bool:
        """Platform-only stamped ingress; not a carrier or provider argument."""
        if error is None:
            result = copy_json(result)
            if len(encode(result).encode()) > MAX_RESULT_BYTES:
                result, error = None, 'FORMAT_ERROR'
        elif error not in RETRY_CLASS and error not in FATAL and error != 'FORMAT_ERROR':
            raise ContractError('unsupported model error classification')
        delay = 0 if retry_after_seconds is None else seconds_ns(retry_after_seconds)
        if delay < 0:
            raise ContractError('negative retry-after')
        with self._lock:
            now = received_ns
            if type(now) is not int or now < 0 or now > self.clock.target_ns():
                raise ContractError('invalid trusted model receipt time')
            # A running or expiry-pending attempt always has a deadline.
            if (
                self.state not in ('running', 'expiry_pending')
                or attempt_id != self.identity
                or now >= cast(int, self.deadline)
                or self._reply
            ):
                return False
            self.state = 'running'
            self._reply = dict(received_ns=now, result=result, error=error, retry_after_ns=delay)
            return True

    def seal_receipt(self, attempt_id: str) -> bool:
        with self._lock:
            if attempt_id != self.identity or self.state not in ('running', 'expiry_pending'):
                return False
            self._receipt_sealed = True
            self.state = 'running'
            return True

    def receive(self, attempt_id: str, **reply: Any) -> bool:
        """Trusted same-process ingress; time cannot be supplied by the caller."""
        return self.receive_at(attempt_id, self.clock.target_ns(), **reply)

    def _failed(self, now: int, error: str, retry_after: int = 0) -> AttemptEvent:
        self.deadline = None
        self._reply = None
        attempts = self.count - self._first
        retryable = RETRY_CLASS.get(error) in self.retry['retry_classes']
        if error == 'FORMAT_ERROR':
            self.format_count += 1
            retryable = self.format_count < self.retry['format_max_attempts']
        if not retryable or attempts >= self.retry['max_attempts']:
            self.state = 'failed'
            exhausted = retryable or error == 'FORMAT_ERROR'
            return dict(event='attempt_failed', error='RETRY_EXHAUSTED' if exhausted else error)
        delay = min(
            seconds_ns(self.retry['max_delay_seconds']),
            seconds_ns(self.retry['base_delay_seconds']) * 2 ** (attempts - 1),
        )
        self.retry_due = now + max(delay, retry_after)
        self.state = 'retrying'
        return dict(event='attempt_retry', error=error, retry_due_ns=str(self.retry_due))

    def step(self, now: int) -> Optional[AttemptEvent]:
        """Settle a received reply, expire a late attempt, or start a due retry."""
        with self._lock:
            # A retrying attempt always has a retry time.
            if self.state == 'retrying' and now >= cast(int, self.retry_due):
                return self._dispatch(now)
            if self.state == 'expiry_pending':
                return None
            if self.state != 'running':
                return None
            if self._reply is not None and self._reply['received_ns'] <= now:
                reply, self._reply = self._reply, None
                if reply['error'] is not None:
                    return self._failed(now, reply['error'], reply['retry_after_ns'])
                self.state = 'completed'
                self.deadline = None
                return dict(
                    event='attempt_completed',
                    attempt_id=self.identity,
                    received_ns=str(reply['received_ns']),
                    result=reply['result'],
                )
            # The attempt is running here, so its deadline is set.
            if now >= cast(int, self.deadline):
                if self.require_receipt_seal and not self._receipt_sealed:
                    self.state = 'expiry_pending'
                    return dict(event='attempt_expiry_pending', attempt_id=self.identity)
                return self._failed(now, 'MODEL_TIMEOUT')
            return None

    def end(self) -> None:
        with self._lock:
            self.state = 'stopped'
            self.deadline = self.retry_due = None
            self._reply = None

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return dict(
                state=self.state,
                attempt_id=self.identity,
                deadline_ns=self.deadline,
                retry_due_ns=self.retry_due,
            )
