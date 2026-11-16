"""Child-side work receive capability; direct Runtime pipe, no App relay.

Only next_work/finish/wait. No observation, broker, clock writer, SQL or control
capability. Assembly injects this endpoint into the carrier host.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional

from sim_platform.experiments.scheduling.protocol import MAX_BYTES, packet, unpack, reply
from sim_platform.domain.models import ContractError

if TYPE_CHECKING:  # Annotation only.
    from multiprocessing.connection import Connection

    from sim_platform.experiments.scheduling.clock import ClockReader, StopSignal

# How long one wait for a Runtime reply lasts before the stop flag is checked again.
REPLY_POLL_SECONDS = 0.05

# A delivered work item: 'work_id', 'epoch', 'reason', 'notifications',
# 'scheduled_due_ns' and 'dispatched_ns'.
Work = Dict[str, Any]


class RuntimeClient:
    """The Agent child's request/reply endpoint to the Runtime process.

    Borrowed once from ExperimentRuntimeHandle.work_client() during assembly and
    passed to the Agent's carrier host, which closes it. Requests are sent one at
    a time and each waits for its reply.
    """

    def __init__(
        self, outgoing: Connection, incoming: Connection, stop: StopSignal, clock: ClockReader
    ) -> None:
        self._outgoing, self._incoming = outgoing, incoming
        self._stop, self._clock = stop, clock
        self._number = 0

    @property
    def stopping(self) -> bool:
        """Whether Runtime has closed this agent's work capability."""
        return self._stop.is_set()

    def _request(self, name: str, data: Dict[str, Any]) -> Any:
        if self._stop.is_set():
            raise ContractError('runtime stopping')
        self._number += 1
        identity = str(self._number)
        self._outgoing.send_bytes(packet(dict(id=identity, command=name, data=data)))
        while not self._stop.is_set():
            if not self._incoming.poll(REPLY_POLL_SECONDS):
                continue
            try:
                result = reply(unpack(self._incoming.recv_bytes(MAX_BYTES)))
            except (EOFError, OSError):
                raise ContractError('runtime stopping') from None
            if result['kind'] != 'reply' or result['id'] != identity:
                raise ContractError('unexpected work reply')
            if result['error'] is not None:
                raise ContractError(result['error']['code'])
            return result['result']
        raise ContractError('runtime stopping')

    def next_work(self) -> Work:
        return self._request('next_work', {})

    def finish(self, work: Work) -> Dict[str, Any]:
        return self._request('finish', {key: work[key] for key in ('work_id', 'epoch')})

    def wait(self, work: Work, request_id: str, conditions: List[Dict[str, Any]]) -> Dict[str, Any]:
        return self._request(
            'wait',
            dict(
                work_id=work['work_id'],
                epoch=work['epoch'],
                request_id=request_id,
                conditions=conditions,
            ),
        )

    def begin_model(self, work: Work) -> Dict[str, Any]:
        return self._request('begin_attempt', {key: work[key] for key in ('work_id', 'epoch')})

    def model_receipt(self, attempt_id: str, received_ns: Optional[int], **values: Any) -> Any:
        return self._request(
            'model_receipt', dict(attempt_id=attempt_id, received_ns=received_ns, **values)
        )

    def inspect(self) -> Dict[str, Any]:
        return self._request('inspect', {})

    def fail(self, code: str) -> Dict[str, Any]:
        """Stop this agent's work after a named local failure; keep financial orders alive."""
        return self._request('agent_failed', dict(reason=code))

    def target_ns(self) -> int:
        return self._clock.target_ns()

    def close(self) -> None:
        self._outgoing.close()
        self._incoming.close()
