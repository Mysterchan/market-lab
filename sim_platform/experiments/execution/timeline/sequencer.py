"""Atomic receipt/queue admission and exclusive execution watermark.

A claim at t seals only times < t. Receipts stamped exactly t remain admissible
and wait for a later claim. Never reserve future time or backdate to pipe arrival.
"""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import Any, Callable, Dict, List, Optional, Tuple, cast

from sim_platform.domain.json_values import copy_json, encode
from sim_platform.domain.models import ContractError, integer

# Defaults: commands awaiting a result, and commands remembered for idempotent retries.
DEFAULT_COMMAND_CAPACITY = 64
DEFAULT_RETAINED_COMMANDS = 10000
# Longest request ID, largest encoded payload, and total retained payload bytes.
REQUEST_ID_MAX_LENGTH = 128
MAX_PAYLOAD_BYTES = 65536
MAX_RETAINED_BYTES = 4 * 1024 * 1024

# A command's result: at least 'status', and 'reason' when it did not run.
CommandResult = Dict[str, Any]


@dataclass(frozen=True)
class Command:
    """One admitted request, stamped with its receipt time and admission order."""

    request_id: str
    time_ns: int
    sequence: int
    payload_json: str

    @property
    def payload(self) -> Any:
        import json

        return json.loads(self.payload_json)


@dataclass(frozen=True)
class Batch:
    """Commands claimed together; every command stamped before ``until_ns``."""

    number: int
    until_ns: int  # exclusive
    commands: Tuple[Command, ...]


class Sequencer:
    """Admits commands in receipt order and hands them to the execution owner in batches.

    The ExperimentSession creates one per run. Any thread may admit; only the
    execution owner claims, begins, completes and releases. Results are kept for
    retried request IDs until the run ends.

    Args:
        clock: Returns the current experiment time in nanoseconds.
        start_ns: First admissible time.
        end_ns: Time at which the timeline ends (exclusive).
        capacity: Most commands without a result.
        retained: Most commands remembered in total.
    """

    def __init__(
        self,
        clock: Callable[[], int],
        *,
        start_ns: int,
        end_ns: int,
        capacity: int = DEFAULT_COMMAND_CAPACITY,
        retained: int = DEFAULT_RETAINED_COMMANDS,
    ) -> None:
        for value, name in (
            (start_ns, 'start'),
            (end_ns, 'end'),
            (capacity, 'capacity'),
            (retained, 'retained'),
        ):
            integer(value, name)
        if end_ns <= start_ns or not capacity or retained < capacity:
            raise ContractError('invalid timeline limits')
        self.clock, self.start_ns, self.end_ns = clock, start_ns, end_ns
        self.capacity, self.retained = capacity, retained
        self._lock = RLock()
        self._commands: Dict[str, Command] = {}
        self._results: Dict[str, CommandResult] = {}
        self._queue: List[Command] = []
        self._bytes = 0
        self._sealed = self._last_clock = start_ns
        self._batch: Optional[Batch] = None
        self._batch_number = 0
        self._current: Optional[Command] = None
        self._state = 'running'
        self._paused = False
        self._pause_cutoff: Optional[int] = None

    def _now(self) -> int:
        now = self.clock()
        integer(now, 'active time')
        if now < self._last_clock:
            raise ContractError('active clock moved backwards')
        self._last_clock = now
        return now

    def admit(self, request_id: str, payload: Any) -> Command:
        """Stamp and queue a command; a repeated ID with the same payload returns the original."""
        payload = encode(copy_json(payload))
        if (
            not isinstance(request_id, str)
            or not request_id
            or len(request_id) > REQUEST_ID_MAX_LENGTH
            or len(payload.encode()) > MAX_PAYLOAD_BYTES
        ):
            raise ContractError('invalid command envelope')
        with self._lock:
            previous = self._commands.get(request_id)
            if previous is not None:
                if previous.payload_json != payload:
                    raise ContractError('REQUEST_ID_CONFLICT')
                return previous
            if self._state != 'running' or self._paused:
                raise ContractError('timeline closed to new commands')
            if len(self._commands) - len(self._results) >= self.capacity:
                raise ContractError('command capacity exhausted')
            if (
                len(self._commands) >= self.retained
                or self._bytes + len(payload.encode()) > MAX_RETAINED_BYTES
            ):
                raise ContractError('retained command capacity exhausted')
            # Stamp and append are one lock section; claim cannot pass between them.
            now = self._now()
            if not self.start_ns <= now < self.end_ns or now < self._sealed:
                raise ContractError('command outside active timeline')
            command = Command(request_id, now, len(self._commands) + 1, payload)
            self._commands[request_id] = command
            self._queue.append(command)
            self._bytes += len(payload.encode())
            return command

    def _terminate(self, reason: str) -> None:
        self._state = reason
        for identity in self._commands:
            if identity not in self._results and (
                self._current is None or identity != self._current.request_id
            ):
                self._results[identity] = dict(status='cancelled', reason=reason)
        self._queue.clear()

    def claim(self) -> Optional[Batch]:
        """Seal time before now and take the queued commands stamped before it; None at end."""
        with self._lock:
            if self._batch is not None:
                raise ContractError('only one execution owner may hold a batch')
            if self._state != 'running':
                raise ContractError('timeline is not running')
            now = self._now()
            if now >= self.end_ns:
                self._terminate('end')
                return None
            if self._paused:
                # pause() sets the cutoff before it sets _paused.
                now = cast(int, self._pause_cutoff) + 1
            self._sealed = now
            ready = tuple(c for c in self._queue if c.time_ns < now)
            self._queue = [c for c in self._queue if c.time_ns >= now]
            self._batch_number += 1
            self._batch = Batch(self._batch_number, now, ready)
            return self._batch

    def pause(self, freeze: Callable[[], None]) -> int:
        """Freeze clock and close admission under the same receipt arbitration lock."""
        with self._lock:
            if self._state != 'running':
                raise ContractError('cannot pause a terminated timeline')
            freeze()
            self._pause_cutoff = self._now()
            self._paused = True
            return self._pause_cutoff

    def resume(self, resume: Callable[[], None]) -> int:
        with self._lock:
            if self._state != 'running':
                raise ContractError('cannot resume a terminated timeline')
            resume()
            self._paused = False
            return self._now()

    def execution_active(self) -> bool:
        with self._lock:
            if self._state == 'running' and self._now() >= self.end_ns:
                self._terminate('end')
            return self._state == 'running'

    def begin(self, command: Command) -> bool:
        with self._lock:
            if not self.execution_active():
                return False
            if (
                self._batch is None
                or command not in self._batch.commands
                or self._current is not None
            ):
                raise ContractError('command is not owned by the current batch')
            if command.request_id in self._results:
                raise ContractError('command already completed')
            self._current = command
            return True

    def complete(self, command: Command, result: CommandResult) -> None:
        value = copy_json(result)
        with self._lock:
            if self._current != command:
                raise ContractError('completion does not match active command')
            self._results[command.request_id] = value
            self._current = None

    def release(self, batch: Batch) -> None:
        with self._lock:
            if self._batch is not batch or self._current is not None:
                raise ContractError('invalid batch release')
            self._batch = None

    def stop(self, reason: str = 'stop') -> None:
        """End admission: 'stop' (requested), 'failed' or 'budget' (model budget spent)."""
        if reason not in ('stop', 'failed', 'budget'):
            raise ContractError('invalid terminal reason')
        with self._lock:
            self._terminate(reason)

    def result(self, request_id: str) -> Optional[CommandResult]:
        with self._lock:
            if request_id not in self._commands:
                raise ContractError('unknown request ID')
            return copy_json(self._results.get(request_id))

    def queued_at_or_after(self, time_ns: int) -> bool:
        """Whether a queued command waits for the clock to pass ``time_ns`` (turn pacing)."""
        with self._lock:
            return any(c.time_ns >= time_ns for c in self._queue)

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return dict(
                state=self._state,
                sealed_before_ns=self._sealed,
                pending=len(self._commands) - len(self._results),
                retained=len(self._commands),
            )
