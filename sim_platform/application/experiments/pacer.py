"""Turn-based pacing: the market waits while the Agent works, then moves to its next turn.

In a turn-based Agent experiment the shared clock never moves with real time. The
TurnPacer, a thread in the owning process, advances it. It moves the clock only when the
whole experiment is settled at the current time:

- the execution owner has processed every market event up to it;
- the Runtime has received every market notification published so far;
- the Runtime has fully stepped at this time, so any work that was due or triggered has
  already started; and
- the Agent has no work running and no model reply outstanding.

An order or cancel the Agent submits while the market waits is stamped with the current
time, and the owner executes commands stamped before its clock. So whenever commands are
pending, the pacer moves the clock on by one nanosecond: the command executes at the same
instant, and the Agent gets its result without the market moving.

Then it jumps to the earliest of: the Runtime's next own time (scheduled work, a wait,
a retry, the end), the next market event, or the experiment end. The execution owner
processes every event in between in order, exactly as in Live mode. While the Agent
works, market time stands still, so its thinking and model latency cost nothing.

A model request whose reply has not arrived within the attempt timeout in real time is
given its deadline: the clock jumps to it so the Runtime times the attempt out.
"""

from __future__ import annotations
import logging

import time
from threading import Event, Thread
from typing import TYPE_CHECKING, Any, Callable, Dict, Optional, Tuple

_log = logging.getLogger(__name__)

if TYPE_CHECKING:  # Annotation only.
    from sim_platform.experiments.execution.session import ExperimentSession
    from sim_platform.experiments.scheduling.clock import SharedExperimentClock

# How often the pacer looks at the experiment (seconds).
POLL_SECONDS = 0.002
TERMINAL = ('completed', 'stopped', 'failed')
# Attempt states with a model reply still outstanding.
WAITING_FOR_MODEL = ('running', 'expiry_pending')


class TurnPacer:
    """Advance a turn-based experiment clock between the Agent's turns.

    Args:
        clock: The experiment's turn-based SharedExperimentClock (paced=True).
        session: The execution owner whose market events the clock releases.
        runtime: Returns the Runtime's latest published snapshot.
        published: Returns how many market notification batches have been published
            to the Runtime.
        timeout_ns: The model attempt timeout, applied in real time while paced.
    """

    def __init__(
        self,
        clock: SharedExperimentClock,
        session: ExperimentSession,
        runtime: Callable[[], Dict[str, Any]],
        published: Callable[[], int],
        timeout_ns: int,
    ) -> None:
        self.clock, self.owner = clock, session
        self._runtime, self._published = runtime, published
        self._timeout_ns = timeout_ns
        self._stop = Event()
        self._thread: Optional[Thread] = None
        # (attempt id, real time it was first seen outstanding).
        self._outstanding: Optional[Tuple[str, int]] = None
        self.turns = 0
        self.error: Optional[BaseException] = None

    def start(self) -> None:
        if self._thread is None:
            self._thread = Thread(target=self._run, name='turn-pacer', daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=1)

    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                if self.owner.snapshot()['state'] in TERMINAL:
                    return
                target = self.next_target()
                if target is not None:
                    self.clock.advance_to(target)
                    self.turns += 1
                    self.owner.wake()
                else:
                    self._stop.wait(POLL_SECONDS)
        except BaseException as exc:  # Surfaced through snapshot(); the run then stalls visibly.
            _log.warning('Handled failure in _run', exc_info=True)
            self.error = exc

    def next_target(self) -> Optional[int]:
        """The time to advance to now, or None while the experiment is not settled."""
        if self.owner.snapshot()['state'] != 'running':
            return None
        now = self.clock.target_ns()
        end = self.owner.end_ns
        if now >= end:
            return None
        timeline, reader = self.owner.timeline, self.owner.reader
        upcoming = (
            reader.event_time(timeline.cursor)
            if timeline.cursor < reader.manifest['frames']
            else None
        )
        if upcoming is not None and upcoming < now:
            return None  # The owner is still processing market events before now.
        if upcoming == now:
            # Like commands, an event executes once the clock is past its time.
            return now + 1
        if self.owner.sequencer.queued_at_or_after(now):
            # The owner executes commands stamped before the current time; one nanosecond
            # lets the Agent's order or cancel made at this instant execute now, while it
            # waits for the result.
            return now + 1
        if self.owner.sequencer.snapshot()['pending']:
            return None  # A command is executing; its result reaches the Agent next.
        runtime = self._runtime()
        stepped = runtime.get('stepped_ns')
        if stepped is None or int(stepped) < now:
            return None
        if int(runtime.get('event_sequence', 0)) < self._published():
            return None
        attempt = runtime.get('attempt') or {}
        if attempt.get('state') in WAITING_FOR_MODEL:
            return self._model_deadline(attempt)
        self._outstanding = None
        if attempt.get('state') == 'retrying' and attempt.get('retry_due_ns') is not None:
            return max(now + 1, int(attempt['retry_due_ns']))
        if (runtime.get('work') or {}).get('state') == 'running':
            return None
        times = [end]
        if runtime.get('next_ns') is not None:
            times.append(int(runtime['next_ns']))
        if upcoming is not None:
            times.append(upcoming)
        later = [t for t in times if t > now]
        return min(later) if later else None

    def _model_deadline(self, attempt: Dict[str, Any]) -> Optional[int]:
        """The attempt's deadline once it has been outstanding longer than the timeout."""
        identity = str(attempt.get('attempt_id'))
        started = time.monotonic_ns()
        if self._outstanding is None or self._outstanding[0] != identity:
            self._outstanding = (identity, started)
            return None
        if started - self._outstanding[1] < self._timeout_ns or attempt.get('deadline_ns') is None:
            return None
        return int(attempt['deadline_ns'])

    def snapshot(self) -> Dict[str, Any]:
        return dict(turns=self.turns, error=repr(self.error) if self.error else None)
