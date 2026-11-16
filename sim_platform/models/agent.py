"""Trusted Agent-local model IO; Runtime authorizes attempts and retries.

Only metadata/error classifications cross to Runtime. Prompt, credential and
response payload remain in Agent. No App model worker or payload relay is used.
"""

from __future__ import annotations
import logging

import asyncio
import time
from typing import Any, Callable, Dict, List, Optional, Protocol

from sim_platform.domain.models import ContractError
from sim_platform.domain.json_values import encode
from .contracts import (
    MAX_PAYLOAD_BYTES,
    ModelPayload,
    ModelProfile,
    ModelProvider,
    ProviderError,
    validate_input,
)

_log = logging.getLogger(__name__)

# How often the attempt loop re-checks Stop, the deadline and Runtime state.
POLL_SECONDS = 0.005

# A Runtime grant for one model attempt: 'attempt_id' and 'deadline_ns' (decimal text).
Permit = Dict[str, Any]
# Optional check of a model reply; raising ProviderError makes the attempt retryable.
ResponseValidator = Callable[[Any], object]


class ModelRuntime(Protocol):
    """The Runtime work client calls AgentModel makes (RuntimeClient in experiments)."""

    def target_ns(self) -> int: ...

    def begin_model(self, work: Any) -> Permit: ...

    def model_receipt(self, attempt_id: str, received_ns: Optional[int], **values: Any) -> Any: ...

    def inspect(self) -> Dict[str, Any]: ...


class Signal(Protocol):
    """A process event: Stop is checked with is_set(); Running is waited on."""

    def is_set(self) -> bool: ...

    def wait(self, timeout: Optional[float] = None) -> bool: ...


def redact(value: Any, secret: str) -> Any:
    """Replace every occurrence of the secret in a JSON value's text, keys included."""
    if isinstance(value, str):
        return value.replace(secret, '[redacted]') if secret else value
    if isinstance(value, dict):
        return {redact(k, secret): redact(v, secret) for (k, v) in value.items()}
    if isinstance(value, list):
        return [redact(v, secret) for v in value]
    return value


class AgentModel:
    """Model requests inside the Agent process, with attempts granted by Runtime.

    Created once per Agent process after model setup; it holds the profile, secret
    and provider for the process's life. Runtime decides deadlines and retries; this
    object only performs attempts and reports when each reply arrived.

    Args:
        profile: Selected provider/model settings.
        secret: The credential, never sent to Runtime or logs.
        provider: Adapter that performs the network request.
        runtime: Runtime work client that grants attempts.
        running: Set while the experiment is playing.
        stop: Set when the Agent must stop.
    """

    def __init__(
        self,
        profile: ModelProfile,
        secret: str,
        provider: ModelProvider,
        runtime: ModelRuntime,
        running: Signal,
        stop: Signal,
    ) -> None:
        (self.profile, self._secret, self.provider) = (profile, secret, provider)
        (self.runtime, self.running, self.stop) = (runtime, running, stop)

    async def _attempt(
        self,
        permit: Permit,
        payload: ModelPayload,
        validate_response: Optional[ResponseValidator] = None,
        label: Optional[str] = None,
    ) -> Any:
        """Run one attempt until reply, deadline or Stop; report its receipt to Runtime."""
        receipt: List[Optional[int]] = [None]

        def received() -> None:
            if receipt[0] is None:
                receipt[0] = self.runtime.target_ns()

        async def invoke() -> Any:
            if hasattr(self.provider, 'request_received'):
                return await self.provider.request_received(
                    self.profile, payload, self._secret, received
                )
            value = await self.provider.request(self.profile, payload, self._secret)
            received()
            return value

        task = asyncio.create_task(invoke())
        error: Optional[str] = None
        retry_after: Optional[str] = None
        value: Any = None
        try:
            deadline = int(permit['deadline_ns'])
            while not task.done():
                if self.stop.is_set():
                    raise ContractError('agent stopping')
                if self.runtime.target_ns() >= deadline and receipt[0] is None:
                    error = 'MODEL_TIMEOUT'
                    break
                await asyncio.wait((task,), timeout=POLL_SECONDS)
            if error is None:
                try:
                    value = task.result()
                    if receipt[0] is None:
                        received()
                    value = redact(value, self._secret)
                    if len(encode(value).encode()) > MAX_PAYLOAD_BYTES:
                        raise ProviderError('FORMAT_ERROR')
                    if validate_response is not None:
                        validate_response(value)
                except ProviderError as exc:
                    (error, retry_after) = (exc.code, exc.retry_after_seconds)
                except Exception:
                    _log.warning('Handled failure in _attempt', exc_info=True)
                    error = 'TRANSPORT_ERROR'
            if receipt[0] is None:
                received()
            from sim_platform.experiments.scheduling.protocol import model_usage

            # Token counts only; the reply itself never leaves the Agent.
            usage = model_usage(value) if error is None else {}
            if usage and label:
                usage['label'] = label
            extra = {'usage': usage} if usage else {}
            self.runtime.model_receipt(
                permit['attempt_id'],
                receipt[0],
                error=error,
                retry_after_seconds=retry_after,
                **extra,
            )
            return value
        finally:
            if not task.done():
                task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                _log.debug('Handled failure in _attempt', exc_info=True)

    def request(
        self,
        work: Any,
        payload: Any,
        *,
        validate_response: Optional[ResponseValidator] = None,
        label: Optional[str] = None,
    ) -> Any:
        """Request a reply for ``work``, following Runtime's retries; blocks until done.

        ``label`` (a role name, at most 64 characters) is recorded with the reply's usage.

        ``work`` is the Runtime work item being served. Raises ProviderError when Runtime
        ends the model call as failed, and ContractError when the Agent is stopping.
        """
        payload = validate_input(payload)
        permit = self.runtime.begin_model(work)
        while not self.stop.is_set():
            value = asyncio.run(self._attempt(permit, payload, validate_response, label))
            while not self.stop.is_set():
                snapshot = self.runtime.inspect()
                attempt = snapshot['attempt']
                state = attempt['state']
                if state == 'completed':
                    self.running.wait()
                    if self.stop.is_set():
                        raise ContractError('agent stopping')
                    return value
                if state in ('failed', 'stopped'):
                    raise ProviderError(snapshot.get('model_error', 'MODEL_TIMEOUT'))
                if state == 'running' and attempt['attempt_id'] != permit['attempt_id']:
                    permit = dict(
                        attempt_id=attempt['attempt_id'], deadline_ns=str(attempt['deadline_ns'])
                    )
                    break
                time.sleep(POLL_SECONDS)
        raise ContractError('agent stopping')
