"""Agent-process client for tools, Runtime work and Agent-local model setup."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, Callable, Dict, Optional, Set, cast

from sim_platform.agents.configuration import validate_identifier
from sim_platform.domain.models import ContractError


from .protocol import packet, unpack

if TYPE_CHECKING:  # Annotation only.
    from multiprocessing.connection import Connection

    from sim_platform.experiments.scheduling.client import RuntimeClient
    from sim_platform.experiments.scheduling.signals import ProcessSignal
    from sim_platform.models.agent import AgentModel
    from .protocol import TransportLimits

# How long each wait lasts before Stop is checked again.
POLL_SECONDS = 0.05
# Bytes allowed around a request/response for its envelope.
ENVELOPE_BYTES = 512
# Default tool reply timeout, and the wait per round while waiting for work (a day).
DEFAULT_REPLY_TIMEOUT_SECONDS = 30
WORK_WAIT_SECONDS = 86400
# How long a model request waits for the model setup to arrive.
MODEL_SETUP_WAIT_SECONDS = 5


class AgentProcessClient:
    """Child-only synchronous capability; no database, native or platform handle.

    submit/receive support bounded batches for trusted carriers. Requests and
    results retain their existing tool envelope. Cancellation is cooperative;
    capability gates withhold replies during Pause; arbitrary CPU code is not suspended.
    The Agent child creates one in run_program and sets ``work_client`` and
    ``model_setup`` on it before the program starts.
    """

    # Set by run_program after construction; read with getattr where they may be unset.
    work_client: Optional[RuntimeClient]
    model_setup: Optional[Connection]
    _local_model: AgentModel

    def __init__(
        self,
        outgoing: Connection,
        incoming: Connection,
        limits: TransportLimits,
        stop: ProcessSignal,
        running: ProcessSignal,
    ) -> None:
        self._outgoing, self._incoming = outgoing, incoming
        self._limits, self._stop, self._running = limits, stop, running
        self._pending: Set[str] = set()

    def wait_running(self) -> None:
        """Wait while paused; raise once the process is stopping."""
        while not self._stop.is_set():
            if self._running.wait(POLL_SECONDS):
                return
        raise ContractError('agent process stopping')

    def wait_stopped(self) -> None:
        self._stop.wait()

    @property
    def stopping(self) -> bool:
        """Whether experiment Stop/EOF has closed this process's capabilities."""
        work = getattr(self, 'work_client', None)
        return self._stop.is_set() or (work is not None and work.stopping)

    def prepare_model(self) -> None:
        """Wait for the selected model setup without consuming a work round.

        The normal API configures this before Play. An unconfigured template stays
        idle until Stop; the wait neither starts an attempt nor runs a scheduler.
        """
        if hasattr(self, '_local_model'):
            return
        setup = getattr(self, 'model_setup', None)
        if setup is None:
            raise ContractError('Agent-local model capability is not configured')
        while not self.stopping:
            self.wait_running()
            if not setup.poll(POLL_SECONDS):
                continue
            profile, secret, provider = setup.recv()
            setup.close()
            from sim_platform.models.agent import AgentModel

            # A local model is configured only together with a Runtime work client.
            self._local_model = AgentModel(
                profile,
                secret,
                provider,
                cast('RuntimeClient', self.work_client),
                self._running,
                self._stop,
            )
            return
        raise ContractError('agent process stopping')

    def submit(self, request: Any) -> None:
        self.wait_running()
        if type(request) is not dict:
            raise ContractError('tool request must be an object')
        identity: Any = request.get('request_id')
        validate_identifier(identity)
        if identity in self._pending or len(self._pending) >= self._limits.pending:
            raise ContractError('duplicate request or client capacity exceeded')
        packet(request, self._limits.request_bytes)
        raw = packet(dict(request=request), self._limits.request_bytes + ENVELOPE_BYTES)
        self._outgoing.send_bytes(raw)
        self._pending.add(identity)

    def receive(self, timeout: float = DEFAULT_REPLY_TIMEOUT_SECONDS) -> Any:
        """Wait for one reply; paused time does not count toward ``timeout``."""
        if (
            type(timeout) not in (int, float)
            or timeout <= 0
            or not __import__('math').isfinite(timeout)
        ):
            raise ContractError('IPC response timeout must be positive and finite')
        remaining = timeout
        previous = time.monotonic()
        while not self._stop.is_set():
            if self._incoming.poll(POLL_SECONDS):
                message = unpack(
                    self._incoming.recv_bytes(self._limits.response_bytes + ENVELOPE_BYTES)
                )
                if set(message) != {'request_id', 'result', 'error'}:
                    raise ContractError('invalid IPC response')
                identity = message['request_id']
                if identity not in self._pending:
                    raise ContractError('unexpected IPC response identity')
                self._pending.remove(identity)
                # Withhold external results until the capability gate resumes.
                self.wait_running()
                if message['error'] is not None:
                    raise ContractError(message['error'])
                return message['result']
            now = time.monotonic()
            if self._running.is_set():
                remaining -= now - previous
            previous = now
            if remaining <= 0:
                raise ContractError('IPC response wait expired')
        raise ContractError('agent process stopping')

    def next_work(self) -> Dict[str, Any]:
        """Wait cooperatively for one owner-issued work identity and notifications."""
        self.wait_running()
        if self._pending:
            raise ContractError('drain tool replies before waiting for work')
        if getattr(self, 'work_client', None) is not None:
            result = cast('RuntimeClient', self.work_client).next_work()
            self.wait_running()
            return result
        identity = 'next-work'
        self._outgoing.send_bytes(
            packet(
                dict(request_id=identity, command='next_work'),
                self._limits.request_bytes + ENVELOPE_BYTES,
            )
        )
        self._pending.add(identity)
        # Work waiting has no whole-round timeout; Stop remains cooperative.
        while not self._stop.is_set():
            try:
                result = self.receive(timeout=WORK_WAIT_SECONDS)
                if 'runtime_error' in result:
                    raise ContractError(result['runtime_error'])
                return result
            except ContractError as error:
                if str(error) != 'IPC response wait expired':
                    raise
        raise ContractError('agent process stopping')

    def request_model(
        self,
        work: Dict[str, Any],
        payload: Any,
        *,
        validate_response: Optional[Callable[[Any], object]] = None,
        label: Optional[str] = None,
    ) -> Any:
        self.wait_running()
        if self._pending:
            raise ContractError('drain replies before model request')
        if getattr(self, 'model_setup', None) is not None:
            if not hasattr(self, '_local_model'):
                if not cast('Connection', self.model_setup).poll(MODEL_SETUP_WAIT_SECONDS):
                    raise ContractError('Agent model not configured')
                self.prepare_model()
            return self._local_model.request(
                work, payload, validate_response=validate_response, label=label
            )
        raise ContractError('Agent-local model capability is not configured')

    def request_tool(self, request: Dict[str, Any]) -> Any:
        if self._pending:
            raise ContractError('drain batched replies before synchronous request')
        self.submit(request)
        return self.receive()
