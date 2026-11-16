"""Spawn entrypoint: policy runs independently of IO publication and market work."""

from __future__ import annotations
import logging

from concurrent.futures import Future
from queue import Queue, Full, Empty
from threading import Thread
from typing import TYPE_CHECKING, Any, Callable, Dict, Iterator, Optional

from sim_platform.domain.models import ContractError
from sim_platform.experiments.scheduling.protocol import (
    MAX_BYTES,
    Message,
    packet,
    unpack,
    command,
)

_log = logging.getLogger(__name__)

if TYPE_CHECKING:  # Annotation only.
    from multiprocessing.connection import Connection

    from multiprocessing.synchronize import Semaphore

    from sim_platform.experiments.scheduling.clock import ClockReader, StopSignal
    from sim_platform.experiments.scheduling.policy.controller import LiveWorkPolicy

# Publishes a Runtime notice: notice(event, details).
NoticeCallback = Callable[[str, Dict[str, Any]], None]
# Builds the work policy in the child: factory(configuration, clock, notice).
PolicyFactory = Callable[[Dict[str, Any], 'ClockReader', NoticeCallback], 'LiveWorkPolicy']

# Queue sizes: replies/notices per capacity unit plus lifecycle reserve; Agent replies.
OUTPUT_RESERVE = 16
CLIENT_OUTPUT_CAPACITY = 2
# Most messages read per turn from the control, application and Agent channels.
CONTROL_BATCH = 4
APPLICATION_BATCH = 32
AGENT_BATCH = 2
# How long the stopping loop waits for each sender thread to flush.
SENDER_JOIN_SECONDS = 0.5


def send_messages(outgoing: Connection, output: Queue[Optional[Message]]) -> None:
    """Send queued messages until a None sentinel, then close the pipe."""
    try:
        while True:
            message = output.get()
            if message is None:
                return
            outgoing.send_bytes(packet(message))
    finally:
        outgoing.close()


class RuntimeLoop:
    """The Runtime child's policy and output queues; one per Runtime process.

    run_runtime creates it, starts its two sender threads and drives the policy from
    its single loop thread. Replies to the Agent go on a separate small queue.
    """

    def emit(self, **message: Any) -> None:
        try:
            self.output.put_nowait(dict(**message))
        except Full as exc:
            raise ContractError('runtime output capacity exhausted') from exc

    def notice(self, event: str, details: Dict[str, Any]) -> None:
        self.emit(kind='notice', event=event, details=details)

    def __init__(
        self,
        outgoing: Connection,
        client_outgoing: Connection,
        clock: ClockReader,
        factory: PolicyFactory,
        configuration: Dict[str, Any],
        capacity: int,
    ) -> None:
        self.output: Queue[Optional[Message]] = Queue(capacity * 2 + OUTPUT_RESERVE)
        self.client_output: Queue[Optional[Message]] = Queue(CLIENT_OUTPUT_CAPACITY)
        self.sender = Thread(
            target=send_messages, args=(outgoing, self.output), name='runtime-output', daemon=True
        )
        self.client_sender = Thread(
            target=send_messages,
            args=(client_outgoing, self.client_output),
            name='runtime-work-output',
            daemon=True,
        )
        self.policy = factory(configuration, clock, self.notice)
        self._last: Optional[Dict[str, Any]] = None

    def reply(self, identity: str, source: Future[Any], client: bool = False) -> None:
        """Queue the reply for request ``identity`` from a finished Future."""
        error: Optional[Dict[str, Any]] = None
        result: Any = None
        if source.cancelled():
            error = dict(code='RUNTIME_STOPPED', message='runtime stopped before work delivery')
        else:
            try:
                result = source.result()
            except Exception as exc:
                _log.warning('Handled failure in reply', exc_info=True)
                error = dict(code=getattr(exc, 'code', 'CONTRACT_ERROR'), message=str(exc))
        message = dict(
            kind='reply', id=identity, result=result, error=error, snapshot=self.policy.snapshot()
        )
        try:
            (self.client_output if client else self.output).put_nowait(message)
        except Full as exc:
            raise ContractError('runtime reply capacity exhausted') from exc

    def dispatch(self, message: Message, client: bool = False) -> None:
        """Run one command on the policy and queue its reply (now or when it resolves)."""
        item = command(message)
        result: Future[Any] = Future()
        try:
            # A just-resumed clock can become visible before the next loop
            # turn. Dispatch due work before validating an immediate request.
            if item['command'] != 'control':
                self.policy.step()
            if client and item['command'] not in (
                'next_work',
                'finish',
                'wait',
                'begin_attempt',
                'model_receipt',
                'inspect',
                'agent_failed',
            ):
                raise ContractError('work capability does not grant this command')
            value = self.policy.dispatch(item['command'], item['data'])
            if isinstance(value, Future):
                value.add_done_callback(lambda source: self.reply(item['id'], source, client))
                return
            result.set_result(value)
        except (ContractError, KeyError, TypeError, ValueError) as exc:
            result.set_exception(exc)
        self.reply(item['id'], result, client)

    def publish(self) -> None:
        snapshot = self.policy.snapshot()
        # Target time changes continuously; publish policy transitions only.
        state = {key: value for key, value in snapshot.items() if key != 'target_ns'}
        if state != self._last:
            self._last = state
            self.emit(kind='snapshot', snapshot=snapshot)


class RuntimeIngress:
    """IO-only reader; timer loop waits on a shared wake event, never a pipe.

    One per input pipe, owned by RuntimeInputs; its daemon thread reads until the
    pipe fails and then records the error.
    """

    # Set in __init__; declared here because read() is defined first.
    incoming: Connection
    wake: Semaphore
    queue: Queue[Message]
    error: Optional[BaseException]

    def read(self) -> None:
        try:
            while True:
                raw = self.incoming.recv_bytes(MAX_BYTES)
                # recv framing is bounded before decoding/allocating a JSON graph.
                self.queue.put_nowait(unpack(raw))
                self.wake.release()
        except Exception as exc:
            _log.warning('Handled failure in read', exc_info=True)
            self.error = exc
            self.wake.release()

    def drain(self, limit: int, *, stop: Optional[StopSignal] = None) -> Iterator[Message]:
        """Take at most limit queued messages without waiting on the IO thread."""
        for _ in range(limit):
            if stop is not None and stop.is_set():
                return
            try:
                yield self.queue.get_nowait()
            except Empty:
                return

    def __init__(self, incoming: Connection, wake: Semaphore, capacity: int) -> None:
        self.incoming, self.wake = incoming, wake
        self.queue = Queue(capacity)
        self.error = None
        self.thread = Thread(target=self.read, name='runtime-input', daemon=True)


class RuntimeInputs:
    """Separate control, application and Agent channels around the serial policy loop.

    Controls have a reserved queue. Each turn services bounded batches in that
    order, then returns to the timer policy. An Agent disconnect is a policy
    notification; losing either App channel is a Runtime failure.
    """

    def _service_controls(self, loop: RuntimeLoop) -> None:
        if self.control.error is not None:
            raise ContractError('runtime control ingress failed') from self.control.error
        for message in self.control.drain(CONTROL_BATCH):
            if message.get('command') != 'control':
                raise ContractError('non-control on reserved channel')
            loop.dispatch(message)

    def _service_agent(self, loop: RuntimeLoop) -> None:
        if self.agent.error is not None:
            loop.policy.dispatch('agent_failed', dict(reason='work capability disconnected'))
            self.agent.error = None
        for message in self.agent.drain(AGENT_BATCH):
            loop.dispatch(message, client=True)

    def service(self, loop: RuntimeLoop, stop: StopSignal) -> None:
        """Run one bounded input turn; policy timeouts are serviced by the caller next."""
        self._service_controls(loop)
        if self.application.error is not None:
            raise ContractError('runtime ingress failed') from self.application.error
        for message in self.application.drain(APPLICATION_BATCH, stop=stop):
            loop.dispatch(message)
        self._service_agent(loop)

    @property
    def pending(self) -> bool:
        return any(not source.queue.empty() for source in self.sources)

    def start(self) -> None:
        for source in self.sources:
            source.thread.start()

    def __init__(
        self,
        incoming: Connection,
        client_incoming: Connection,
        control_incoming: Connection,
        wake: Semaphore,
        capacity: int,
    ) -> None:
        self.application = RuntimeIngress(incoming, wake, capacity)
        self.agent = RuntimeIngress(client_incoming, wake, AGENT_BATCH)
        self.control = RuntimeIngress(control_incoming, wake, CONTROL_BATCH)
        self.sources = (self.application, self.agent, self.control)


def run_runtime(
    incoming: Connection,
    outgoing: Connection,
    client_incoming: Connection,
    client_outgoing: Connection,
    control_incoming: Connection,
    stop: StopSignal,
    clock: ClockReader,
    factory: PolicyFactory,
    configuration: Dict[str, Any],
    capacity: int,
) -> None:
    """Runtime child entry point: serve inputs and step the policy until stopped."""
    loop = None
    try:
        loop = RuntimeLoop(outgoing, client_outgoing, clock, factory, configuration, capacity)
        inputs = RuntimeInputs(incoming, client_incoming, control_incoming, clock.changed, capacity)
        loop.sender.start()
        loop.client_sender.start()
        inputs.start()
        loop.publish()
        loop.emit(kind='ready')
        while not stop.is_set():
            inputs.service(loop, stop)
            loop.policy.step()
            loop.publish()
            if inputs.pending:
                clock.changed.release()
            if not stop.is_set():
                clock.changed.acquire(timeout=loop.policy.wait_seconds())
        loop.policy.end('runtime stopped')
        loop.publish()
        loop.emit(kind='ended', snapshot=loop.policy.snapshot())
        loop.output.put_nowait(None)
        loop.client_output.put_nowait(None)
        loop.sender.join(SENDER_JOIN_SECONDS)
        loop.client_sender.join(SENDER_JOIN_SECONDS)
    finally:
        incoming.close()
        client_incoming.close()
        control_incoming.close()
        if loop is None:
            outgoing.close()
            client_outgoing.close()
