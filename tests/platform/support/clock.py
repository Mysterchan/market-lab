"""Shared deterministic clock for prepared Runtime/Agent integration tests."""

import multiprocessing
import os
import time
from sim_platform.domain.models import ContractError
from sim_platform.experiments.scheduling.clock import NO_GAPS, SharedExperimentClock, ClockReader


class FakeTime:
    def __init__(self):
        self._value = multiprocessing.get_context('spawn').RawValue('q', 0)
        self._changes = []

    @property
    def ns(self):
        return self._value.value

    @ns.setter
    def ns(self, value):
        self._value.value = value
        for changed in self._changes:
            changed.release()

    def __call__(self):
        return self.ns


class FakeReader(ClockReader):
    def __init__(self, state, lock, changed, now, gaps=NO_GAPS):
        super().__init__(state, lock, changed, gaps)
        self._now = now

    def snapshot(self):
        elapsed, started, running, version = self._read()
        # elapsed is running time; closed-market skips convert it to market time.
        target = self._gaps.market_ns(elapsed + (self._now() - started if running else 0))
        return dict(
            elapsed_ns=elapsed,
            started_ns=started,
            running=bool(running),
            version=version,
            target_ns=target,
        )


class TestClock(SharedExperimentClock):
    __test__ = False

    def __init__(self, origin_ns, now, gaps=NO_GAPS):
        super().__init__(multiprocessing.get_context('spawn'), origin_ns, gaps)
        self._now = now
        now._changes.append(self.changed)

    def reader(self):
        return FakeReader(self._state, self._lock, self.changed, self._now, self._gaps)

    def snapshot(self):
        return self.reader().snapshot()

    def advance_to(self, time_ns):
        """Close a known timeline boundary without depending on wall-clock speed."""
        current = self.target_ns()
        if time_ns < current:
            raise ValueError('Test time cannot move backwards')
        # Advance real time by running time, so a closed-market skip costs no real time.
        self._now.ns += self._gaps.running_ns(time_ns) - self._gaps.running_ns(current)

    def advance_real(self, real_ns):
        """Let ``real_ns`` of experiment running time pass, skipping closed markets."""
        self._now.ns += real_ns

    def _change(self, running):
        if os.getpid() != self._writer:
            raise ContractError('only clock owner can advance virtual time')
        with self._lock:
            elapsed, started, current, version = self._state[:]
            if bool(current) == running:
                return
            now = self._now()
            self._state[:] = [
                elapsed + (now - started if current else 0),
                now if running else 0,
                int(running),
                version + 1,
            ]
        self.changed.release()


def until(predicate, timeout=3, *, advance=None):
    """Wait for completion, optionally advancing virtual time on each poll."""
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError('condition did not complete')
        if advance is not None:
            advance()
        time.sleep(0.001)


def controlled_clock_class(now, clocks):
    """A stand-in for SharedExperimentClock that Platform assembly can construct.

    Patch it over ``sim_platform.experiments.scheduling.clock.SharedExperimentClock`` (or
    the name a module imported) while opening an experiment. Assembly then builds a
    TestClock on ``now`` with the origin and closed-market skips it would have used, and
    appends it to ``clocks``. It is a subclass, so isinstance checks still pass.
    """

    class ControlledClock(TestClock):
        def __init__(self, context, origin_ns, gaps=NO_GAPS):
            super().__init__(origin_ns, now, gaps)
            clocks.append(self)

    return ControlledClock


def act_while_advancing(clock, submit):
    """Run ``submit()`` while nudging the clock, so its receipt time can close."""
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=1) as worker:
        result = worker.submit(submit)
        while not result.done():
            clock.advance_real(1)
            time.sleep(0.002)
        return result.result()
