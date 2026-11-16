"""Parent-written effective clock, read by spawn children over one POSIX epoch.

Clock reads use a bounded process lock: child death while holding it faults the
clock instead of hanging the app. Stop/wake and close never acquire this lock.
The wake semaphore has no process-owned mutex to strand during termination.
"""

from __future__ import annotations

from bisect import bisect_right
import os
import sys
import time
from threading import RLock
from typing import TYPE_CHECKING, Any, Callable, Dict, Iterable, List, Optional, Sequence

from sim_platform.domain.models import ContractError, integer

if TYPE_CHECKING:  # Annotation only.
    from multiprocessing.context import BaseContext
    from multiprocessing.synchronize import Lock, Semaphore

# Longest wait for the shared clock lock; a timeout means a child died holding it.
LOCK_TIMEOUT_SECONDS = 0.1

# A clock reading: 'elapsed_ns', 'started_ns', 'running', 'version' and 'target_ns'.
ClockSnapshot = Dict[str, Any]


# After the last event of a session the clock keeps running this long before it skips
# the closed market, so work already in progress can finish at its normal pace.
GAP_GRACE_NS = 5_000_000_000
# The clock lands this long before the next session's first event.
GAP_LEAD_NS = 2_000_000_000
# A turn-based clock never moves by itself; waiters re-read it at least this often.
PACED_POLL_NS = 20_000_000


class SessionGaps:
    """Stretches of market time, such as nights and weekends, that playback skips.

    A recording that covers several days has no events while the market is closed.
    Running through those hours in real time would be pointless, so the experiment
    clock jumps over them. This class holds the arithmetic for that jump.

    Two time lines are involved. *Market time* is what the recording and every view
    use. *Running time* is market time with the skipped stretches removed: it advances
    exactly as fast as real time while the experiment plays. A clock stores running
    time and converts it to market time whenever it is read.

    Args:
        closed: (start, end) market times between which the recording has no events.
        origin_ns: Where the experiment starts; stretches before it are irrelevant.
        grace_ns: How long after ``start`` the clock still runs normally.
        lead_ns: How long before ``end`` the clock resumes.
    """

    def __init__(
        self,
        closed: Iterable[Sequence[int]] = (),
        origin_ns: int = 0,
        *,
        grace_ns: int = GAP_GRACE_NS,
        lead_ns: int = GAP_LEAD_NS,
    ) -> None:
        self._starts: List[int] = []
        self._ends: List[int] = []
        # Running time at which each skip happens, and market time skipped up to and
        # including it.
        self._running_starts: List[int] = []
        self._skipped: List[int] = [0]
        for start, end in sorted((int(a), int(b)) for a, b in closed):
            start, end = max(start + grace_ns, origin_ns), end - lead_ns
            if end <= start or (self._ends and start < self._ends[-1]):
                continue
            self._starts.append(start)
            self._ends.append(end)
            self._running_starts.append(start - self._skipped[-1])
            self._skipped.append(self._skipped[-1] + end - start)

    def __bool__(self) -> bool:
        return bool(self._starts)

    def market_ns(self, running_ns: int) -> int:
        """Convert running time to market time; at a skip the result is the far side."""
        return running_ns + self._skipped[bisect_right(self._running_starts, running_ns)]

    def running_ns(self, market_ns: int) -> int:
        """Convert market time to running time; any time inside a skip maps to its start."""
        passed = bisect_right(self._ends, market_ns)
        if passed < len(self._starts) and market_ns > self._starts[passed]:
            return self._running_starts[passed]
        return market_ns - self._skipped[passed]


NO_GAPS = SessionGaps()


def session_gaps(
    manifest: Dict[str, Any], origin_ns: int, timeout_seconds: Any = None
) -> SessionGaps:
    """Build the skips for an experiment on a prepared recording.

    Only a recording prepared from price bars declares closed-market stretches; any
    other recording gives a clock that never skips. ``timeout_seconds`` is the longest
    a piece of work may take: the clock keeps running that much longer after a session
    ends, so work started just before the close is not cut short by the skip.
    """
    closed = (manifest.get('calendar') or {}).get('gaps') or ()
    if not closed:
        return NO_GAPS
    grace = GAP_GRACE_NS + (int(float(timeout_seconds) * 1e9) if timeout_seconds else 0)
    return SessionGaps(closed, origin_ns, grace_ns=grace)


def real_delay_ns(clock: Any, until_ns: int) -> int:
    """Real nanoseconds until the clock reaches ``until_ns`` if it keeps running.

    A plain difference of market times would count hours the clock is going to skip.
    """
    delay = getattr(clock, 'delay_ns', None)
    return delay(until_ns) if delay is not None else max(0, until_ns - clock.target_ns())


def monotonic_ns() -> int:
    """Read a monotonic clock that all processes of the experiment share.

    POSIX: CLOCK_MONOTONIC. Windows: QueryPerformanceCounter (time.monotonic_ns), one
    machine-wide counter, so values from different processes compare directly.
    """
    if hasattr(time, 'CLOCK_MONOTONIC'):
        return time.clock_gettime_ns(time.CLOCK_MONOTONIC)
    if sys.platform == 'win32':
        return time.monotonic_ns()
    raise ContractError('shared runtime clock requires a system-wide monotonic clock')


class StopSignal:
    """One parent-written byte: termination cannot inherit a killed child's lock."""

    def __init__(self, context: BaseContext) -> None:
        self._value = context.RawValue('b', 0)

    def is_set(self) -> bool:
        return bool(self._value.value)

    def set(self) -> None:
        self._value.value = 1


class ClockReader:
    """Read-only view of a SharedExperimentClock, safe to pass to spawned children.

    Created by SharedExperimentClock.reader(); it borrows the clock's shared state,
    lock and wake semaphore and owns nothing.

    Args:
        state: Shared ctypes array of four int64 values: elapsed, started, running, version.
            ``elapsed`` is running time (see SessionGaps), not market time.
        lock: Process lock guarding ``state``.
        changed: Semaphore released whenever the clock or Runtime inputs change.
        gaps: Closed-market stretches this clock skips.
        paced: A turn-based clock: market time moves only when the owner advances it
            (advance_to), never with real time. ``running`` still says whether the
            experiment is playing.
    """

    def __init__(
        self,
        state: Any,
        lock: Lock,
        changed: Semaphore,
        gaps: SessionGaps = NO_GAPS,
        paced: bool = False,
    ) -> None:
        self._state, self._lock, self.changed = state, lock, changed
        self._gaps = gaps
        self.paced = paced

    def _read(self) -> Any:
        if not self._lock.acquire(timeout=LOCK_TIMEOUT_SECONDS):
            raise ContractError('shared clock unavailable; possible runtime failure')
        try:
            return self._state[:]
        finally:
            self._lock.release()

    def snapshot(self) -> ClockSnapshot:
        elapsed, started, running, version = self._read()
        return dict(
            elapsed_ns=elapsed,
            started_ns=started,
            running=bool(running),
            version=version,
            target_ns=self._gaps.market_ns(
                elapsed + (monotonic_ns() - started if running and not self.paced else 0)
            ),
        )

    def target_ns(self) -> int:
        """Current experiment time in market time, with closed stretches skipped."""
        return self.snapshot()['target_ns']

    def delay_ns(self, until_ns: int) -> int:
        """Real nanoseconds until this clock reaches ``until_ns`` if it keeps running.

        Derived from target_ns(), the one reading every user of the clock shares, so a
        clock that supplies its own time source stays consistent. A turn-based clock only
        moves when advanced, so a waiter is told to look again shortly instead.
        """
        now = self._gaps.running_ns(self.target_ns())
        delay = max(0, self._gaps.running_ns(until_ns) - now)
        return min(delay, PACED_POLL_NS) if self.paced else delay


class SharedExperimentClock(ClockReader):
    """Experiment time shared with child processes; only the creating process changes it.

    Assembly creates one per Agent experiment and passes reader() copies to the
    Runtime and Agent children. It starts paused at ``origin_ns``. ``gaps`` lists the
    closed-market stretches of a multi-day recording, which the clock skips. ``paced``
    makes it turn-based: time moves only through advance_to(), driven by a TurnPacer.
    """

    def __init__(
        self,
        context: BaseContext,
        origin_ns: int,
        gaps: SessionGaps = NO_GAPS,
        *,
        paced: bool = False,
    ) -> None:
        integer(origin_ns, 'clock origin')
        super().__init__(
            context.RawArray('q', [gaps.running_ns(origin_ns), 0, 0, 0]),
            context.Lock(),
            context.Semaphore(0),
            gaps,
            paced,
        )
        self._writer = os.getpid()

    def reader(self) -> ClockReader:
        return ClockReader(self._state, self._lock, self.changed, self._gaps, self.paced)

    def advance_to(self, market_ns: int) -> None:
        """Move a turn-based clock forward to ``market_ns`` (never backwards) and wake waiters."""
        integer(market_ns, 'clock target')
        if not self.paced:
            raise ContractError('only a turn-based clock is advanced explicitly')
        if os.getpid() != self._writer:
            raise ContractError('only the clock owner may change experiment time')
        if not self._lock.acquire(timeout=LOCK_TIMEOUT_SECONDS):
            raise ContractError('shared clock unavailable; possible runtime failure')
        try:
            elapsed, started, running, version = self._state[:]
            target = self._gaps.running_ns(market_ns)
            if target <= elapsed:
                return
            self._state[:] = [target, started, running, version + 1]
        finally:
            self._lock.release()
        self.changed.release()

    def _change(self, running: bool) -> None:
        if os.getpid() != self._writer:
            raise ContractError('only the clock owner may change experiment time')
        if not self._lock.acquire(timeout=LOCK_TIMEOUT_SECONDS):
            raise ContractError('shared clock unavailable; possible runtime failure')
        try:
            elapsed, started, current, version = self._state[:]
            if bool(current) == running:
                return
            now = monotonic_ns()
            self._state[:] = [
                elapsed + (now - started if current and not self.paced else 0),
                now if running else 0,
                int(running),
                version + 1,
            ]
        finally:
            self._lock.release()
        self.changed.release()

    def resume(self) -> None:
        self._change(True)

    def pause(self) -> None:
        self._change(False)


class ExperimentClock:
    """In-process experiment time for policy experiments (no child processes).

    The execution pipeline creates one per run when no shared clock is supplied.
    It starts paused at ``origin_ns``; ``now`` supplies monotonic nanoseconds. ``gaps``
    lists the closed-market stretches of a multi-day recording, which the clock skips.
    """

    def __init__(
        self,
        origin_ns: int,
        now: Callable[[], int] = monotonic_ns,
        gaps: SessionGaps = NO_GAPS,
    ) -> None:
        integer(origin_ns, 'clock origin')
        self._now = now
        self._lock = RLock()
        self._gaps = gaps
        # Running time (see SessionGaps); converted to market time when read.
        self._elapsed = gaps.running_ns(origin_ns)
        self._started: Optional[int] = None
        self._previous: Optional[int] = None

    def _read(self) -> int:
        value = self._now()
        integer(value, 'monotonic time')
        if self._previous is not None and value < self._previous:
            raise ContractError('monotonic clock moved backwards')
        self._previous = value
        return value

    def _running_ns(self) -> int:
        with self._lock:
            now = self._read()
            return self._elapsed + (0 if self._started is None else now - self._started)

    def target_ns(self) -> int:
        """Current experiment time in market time, with closed stretches skipped."""
        return self._gaps.market_ns(self._running_ns())

    def delay_ns(self, until_ns: int) -> int:
        """Real nanoseconds until this clock reaches ``until_ns`` if it keeps running.

        Derived from target_ns(), the one reading every user of the clock shares, so a
        clock that supplies its own time source stays consistent.
        """
        now = self._gaps.running_ns(self.target_ns())
        return max(0, self._gaps.running_ns(until_ns) - now)

    def resume(self) -> None:
        with self._lock:
            if self._started is None:
                self._started = self._read()

    def pause(self) -> None:
        with self._lock:
            if self._started is not None:
                self._elapsed += self._read() - self._started
                self._started = None
