"""Shared execution ordering, capacity and failure tests using controlled clocks."""

from bisect import bisect_right
from threading import Event
from types import SimpleNamespace
import unittest
from sim_platform.domain.models import ContractError
from sim_platform.experiments.scheduling.clock import ExperimentClock
from sim_platform.experiments.execution.session import ExperimentSession
from support.clock import FakeTime, until


class Source:
    def __init__(self, times):
        self.times = times
        self.manifest = dict(frames=len(times), last_ns=times[-1])

    def prefix(self, stamp):
        return bisect_right(self.times, stamp)

    def event_time(self, index):
        return self.times[index]

    def source_sequence(self, prefix):
        return prefix


class Driver:
    def __init__(self, times):
        self.reader = Source(times)
        self.market = self
        self.sequence = self.merged_prefix = self.time_ns = 0
        self.status = 'running'
        self.applied = []

    def play(self):
        pass

    def pause(self):
        pass

    def pump(self):
        self.time_ns = self.reader.event_time(self.sequence)
        self.sequence += 1
        self.merged_prefix = self.sequence
        self.applied.append(self.time_ns)
        return (SimpleNamespace(time_ns=self.time_ns, sequence=self.sequence),)

    def finish(self, status, reason, time_ns):
        self.status = status


class PipelineTests(unittest.TestCase):
    def create(
        self, operation=lambda request, position: request, *, sink=None, capacity=4, end=500
    ):
        self.now = FakeTime()
        self.driver = Driver([10, 30, 500, 1000])
        self.messages = []
        kernel = ExperimentSession(
            self.driver,
            self.driver.reader,
            clock=ExperimentClock(0, self.now),
            start_ns=0,
            end_ns=end,
            sink=sink or self.messages.append,
            operation=operation,
            capacity=capacity,
            lag_limit_ns=1000,
        )
        self.addCleanup(kernel.close)
        kernel.play()
        return kernel

    def test_initial_fault_survives_secondary_cleanup_failures(self):
        from unittest.mock import patch

        def fail(request, position):
            raise ValueError('initial financial failure')

        kernel = self.create(fail)
        self.now.ns = 20
        result = kernel.submit({'operation': 'fault'})
        self.now.ns = 21
        with patch.object(self.driver, 'finish', side_effect=OSError('finish cleanup failed')):
            kernel.tick()
        state = kernel.snapshot()
        self.assertEqual(state['state'], 'failed')
        self.assertEqual(state['error'], 'initial financial failure')
        self.assertEqual(state['cleanup_errors'], ['finish cleanup failed'])
        with self.assertRaises(ContractError):
            result.result(2)

    def test_clock_freezes_pause_and_rejects_backwards_source(self):
        now = FakeTime()
        clock = ExperimentClock(100, now)
        clock.resume()
        now.ns = 20
        self.assertEqual(clock.target_ns(), 120)
        clock.pause()
        now.ns = 500
        self.assertEqual(clock.target_ns(), 120)
        clock.resume()
        now.ns = 510
        self.assertEqual(clock.target_ns(), 130)
        now.ns = 509
        with self.assertRaises(ContractError):
            clock.target_ns()

    def test_receipt_tie_is_source_first_and_exclusive_watermark_never_backdates(self):
        seen = []
        kernel = self.create(
            lambda request, position: seen.append((list(self.driver.applied), position)) or {}
        )
        self.now.ns = 30
        result = kernel.submit({'operation': 'one'})
        kernel.tick()
        self.assertFalse(result.done())
        self.now.ns = 31
        kernel.tick()
        self.assertEqual(seen[0][0], [10, 30])
        self.assertEqual(result.result(2)['position']['time_ns'], '30')

    def test_slow_delivery_does_not_hold_financial_owner_and_capacity_is_bounded(self):
        entered, release = Event(), Event()

        def sink(message):
            if message['kind'] == 'result':
                entered.set()
                release.wait(2)

        kernel = self.create(sink=sink, capacity=1)
        try:
            self.now.ns = 20
            result = kernel.submit({'operation': 'one'})
            self.now.ns = 21
            kernel.tick()
            self.assertTrue(entered.wait(1))
            with self.assertRaises(ContractError):
                kernel.submit({'operation': 'two'})
            self.now.ns = 40
            kernel.tick()
            self.assertEqual(self.driver.applied, [10, 30])
        finally:
            release.set()
        result.result(2)

    def test_end_excludes_boundary_source_and_invalidates_unstarted_commands(self):
        calls = []
        kernel = self.create(lambda request, position: calls.append(request) or {})
        self.now.ns = 499
        result = kernel.submit({'operation': 'late'})
        self.now.ns = 500
        kernel.tick()
        self.assertEqual(self.driver.applied, [10, 30])
        self.assertEqual(calls, [])
        with self.assertRaises(ContractError):
            result.result(2)
        self.assertEqual(kernel.snapshot()['state'], 'completed')

    def test_operation_failure_cancels_pending_and_seals_failed_terminal(self):
        def broken(request, position):
            raise OSError('financial failure')

        kernel = self.create(broken)
        self.now.ns = 20
        result = kernel.submit({'operation': 'broken'})
        self.now.ns = 21
        kernel.tick()
        with self.assertRaises(ContractError):
            result.result(2)
        self.assertEqual(kernel.channels.ended.result(2)['status'], 'failed')

    def test_pause_buffers_delivery_and_resume_uses_the_same_clock(self):
        kernel = self.create()
        self.now.ns = 20
        kernel.channels.pause_delivery()
        result = kernel.submit({'operation': 'one'})
        self.now.ns = 21
        kernel.tick()
        pause = kernel.control('pause')
        kernel.tick()
        pause.result(2)
        self.now.ns = 100
        self.assertEqual(kernel.clock.target_ns(), 21)
        self.assertFalse(result.done())
        resume = kernel.control('resume')
        kernel.tick()
        resume.result(2)
        self.assertEqual(result.result(2)['position']['time_ns'], '20')

    def test_result_sink_failure_is_reported_and_resources_drain(self):
        def broken(message):
            raise OSError('disk full')

        kernel = self.create(sink=broken)
        kernel.publish_notice('test', {})
        until(lambda: kernel.channels.output_error is not None)
        kernel.tick()
        with self.assertRaises(ContractError):
            kernel.channels.ended.result(2)
        with self.assertRaises(ContractError):
            kernel.close()
        # Cleanup can recheck drain without suppressing the reported error.
        self._cleanups.pop()
        self.assertTrue(kernel.quiescent)
