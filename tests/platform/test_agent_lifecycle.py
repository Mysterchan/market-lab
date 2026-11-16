"""Portable Agent lifecycle acceptance through real spawn, IPC and descendant programs.

This suite intentionally needs neither the native market library nor an App database.
Run it unchanged on each claimed OS; passing on one OS is not evidence for another.
"""

from concurrent.futures import Future
import ctypes
import multiprocessing
import os
from pathlib import Path
import signal
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import enumerate as threads
import time
import unittest
from unittest.mock import patch

from sim_platform.agents.hosting.host import AgentProcessHost
from sim_platform.agents.hosting.lifecycle import AgentStartupError
from sim_platform.agents.hosting.protocol import TransportLimits
from sim_platform.domain.models import ContractError
from support.failures import expected_child_failure

GRANDCHILD = '''import os, signal, sys, time
from pathlib import Path
if os.name == 'posix': signal.signal(signal.SIGTERM, signal.SIG_IGN)
Path(sys.argv[1], 'grandchild.pid').write_text(str(os.getpid()))
while True: time.sleep(0.01)
'''
CHILD = '''import os, signal, subprocess, sys, time
from pathlib import Path
if os.name == 'posix': signal.signal(signal.SIGTERM, signal.SIG_IGN)
subprocess.Popen([sys.executable, '-c', sys.argv[2], sys.argv[1]])
Path(sys.argv[1], 'child.pid').write_text(str(os.getpid()))
while True: time.sleep(0.01)
'''


def until(predicate, timeout=5, *, diagnostics=None):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            details = '' if diagnostics is None else ': ' + repr(diagnostics())
            raise AssertionError('lifecycle condition did not become true' + details)
        time.sleep(0.01)


def alive(pid):
    if os.name == 'nt':
        from ctypes import wintypes

        api = ctypes.WinDLL('kernel32', use_last_error=True)
        api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        api.OpenProcess.restype = wintypes.HANDLE
        api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        api.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = api.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
        if not handle:
            return False
        try:
            return api.WaitForSingleObject(handle, 0) == 258  # WAIT_TIMEOUT
        finally:
            api.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    stat = Path('/proc') / str(pid) / 'stat'
    if stat.exists():
        # A killed, already orphaned descendant can await the CI container's PID 1 reaper.
        try:
            return stat.read_text().rsplit(')', 1)[1].split()[0] != 'Z'
        except (FileNotFoundError, ProcessLookupError):
            return False
    return True


def spawn_descendants(configuration):
    subprocess.Popen(
        [sys.executable, '-c', CHILD, configuration['directory'], GRANDCHILD],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    until(
        lambda: (Path(configuration['directory']) / 'grandchild.pid').exists()
        and bool((Path(configuration['directory']) / 'grandchild.pid').read_text())
    )


def family_program(client, configuration):
    spawn_descendants(configuration)
    mode = configuration.get('mode')
    if mode == 'crash':
        os._exit(23)
    if mode == 'return':
        return
    client.wait_stopped()


def cpu_program(client, configuration):
    if os.name == 'posix':
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    Path(configuration['directory'], 'busy').touch()
    value = 0
    while True:
        value = (value + 1) % 100000


def gated_program(client, configuration):
    while not client.stopping:
        client.wait_running()
        if client.stopping:
            return
        client.request_tool(dict(request_id='tick-' + str(time.monotonic_ns())))


def blocked_program(client, configuration):
    client.request_tool(dict(request_id='blocked'))
    Path(configuration['directory'], 'unexpected-response').touch()


def malformed_program(client, configuration):
    client._outgoing.send_bytes(b'not-json')
    client.wait_stopped()


def private_contract_program(client, configuration):
    raise ContractError('private-agent-error-content')


class FailingFactory:
    def __init__(self, directory, *, timeout=False):
        self.directory, self.timeout = directory, timeout

    def __call__(self, context):
        spawn_descendants(dict(directory=self.directory))
        if self.timeout:
            time.sleep(30)
        raise RuntimeError('private user exception must not become initialization status')


class AgentLifecycleTests(unittest.TestCase):
    @expected_child_failure(
        'ContractError: private-agent-error-content',
        required=False,
        forbidden=('private-agent-error-content',),
    )
    def test_user_contract_error_reports_failure_without_echoing_private_content(self):
        host = self.host(private_contract_program)
        host.play()
        until(lambda: host.snapshot()['state'] == 'failed', diagnostics=host.snapshot)
        self.assertEqual(host.snapshot()['exitcode'], 1)
        self.assertEqual(host.snapshot()['exit_reason'], 'process_crash')

    def test_disappearing_proc_entry_is_dead_but_other_read_errors_remain_visible(self):
        for error in (FileNotFoundError(), ProcessLookupError()):
            with self.subTest(error=type(error).__name__):
                with patch('os.name', 'posix'), patch('os.kill'), patch(__name__ + '.Path') as path:
                    stat = path.return_value.__truediv__.return_value.__truediv__.return_value
                    stat.exists.return_value = True
                    stat.read_text.side_effect = error
                    self.assertFalse(alive(123))
                    stat.read_text.side_effect = PermissionError('unreadable process state')
                    with self.assertRaises(PermissionError):
                        alive(123)

    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.directory = Path(folder.name)
        self.calls = []
        self.limits = TransportLimits(4096, 4096, 4, 50)

    def submit(self, request):
        self.calls.append(request)
        future = Future()
        future.set_result(dict(result={}))
        return future

    def host(self, program, **options):
        configuration = dict(directory=str(self.directory), mode=options.pop('mode', None))
        host = AgentProcessHost(
            options.pop('submit', self.submit),
            configuration,
            self.limits,
            program=program,
            shutdown_grace=0.1,
            **options,
        )
        self.addCleanup(host.close)
        return host

    def clear_markers(self):
        for name in ('child.pid', 'grandchild.pid'):
            (self.directory / name).unlink(missing_ok=True)

    def descendants(self):
        pids = []

        def captured():
            try:
                pids[:] = [
                    int((self.directory / name).read_text())
                    for name in ('child.pid', 'grandchild.pid')
                ]
                return True
            except (OSError, ValueError):
                return False

        until(captured)
        return pids

    def assert_reaped(self, pids):
        until(lambda: not any(alive(pid) for pid in pids))

    def test_stop_reaps_child_and_grandchild_even_when_they_ignore_terminate(self):
        host = self.host(family_program)
        host.play()
        descendants = self.descendants()
        pid = host.snapshot()['pid']
        started = time.monotonic()
        host.close()
        self.assertLess(time.monotonic() - started, 3)
        self.assert_reaped([pid, *descendants])
        self.assertEqual(host.snapshot()['exit_reason'], 'stop_requested')
        host.close()

    def test_crash_and_normal_return_both_clean_descendants_and_notify_once(self):
        for mode, state, reason in (
            ('crash', 'failed', 'process_crash'),
            ('return', 'completed', 'program_returned'),
        ):
            with self.subTest(mode=mode):
                self.clear_markers()
                notices = []
                host = self.host(
                    family_program, mode=mode, on_exit=lambda *args: notices.append(args)
                )
                host.play()
                descendants = self.descendants()
                until(lambda: host.snapshot()['state'] == state, diagnostics=host.snapshot)
                self.assertEqual(host.snapshot()['exit_reason'], reason)
                host.close()
                self.assert_reaped(descendants)
                self.assertEqual(len(notices), 1)

    def test_closing_one_agent_does_not_stop_another_agent_or_the_app_group(self):
        first = self.host(cpu_program)
        second = self.host(cpu_program)
        first.play()
        second.play()
        until(lambda: (self.directory / 'busy').exists())
        if os.name == 'posix':
            self.assertEqual(os.getpgid(first.snapshot()['pid']), first.snapshot()['pid'])
            self.assertNotEqual(os.getpgrp(), os.getpgid(first.snapshot()['pid']))
        first.close()
        self.assertTrue(alive(second.snapshot()['pid']))
        self.assertEqual(second.snapshot()['state'], 'running')
        second.close()
        self.assert_reaped([first.snapshot()['pid'], second.snapshot()['pid']])

    def test_cpu_loop_is_not_a_crash_and_stop_can_force_it_to_exit(self):
        host = self.host(cpu_program)
        host.play()
        until(lambda: (self.directory / 'busy').exists())
        time.sleep(0.1)
        self.assertEqual(host.snapshot()['state'], 'running')
        host.pause()
        self.assertEqual(host.snapshot()['pause_mode'], 'capability_gate')
        self.assertTrue(alive(host.snapshot()['pid']))
        host.play()
        host.close()
        self.assert_reaped([host.snapshot()['pid']])

    def test_pause_gates_requests_and_resume_reopens_the_same_process(self):
        host = self.host(gated_program)
        pid = host.snapshot()['pid']
        host.play()
        until(lambda: len(self.calls) >= 2)
        host.pause()
        # A request already crossing ingress may complete; admission then settles.
        time.sleep(0.1)
        count = len(self.calls)
        time.sleep(0.1)
        self.assertEqual(len(self.calls), count)
        host.play()
        until(lambda: len(self.calls) > count)
        self.assertEqual(host.snapshot()['pid'], pid)

    def test_stop_discards_late_response_without_invoking_agent_continuation(self):
        pending = Future()
        host = self.host(blocked_program, submit=lambda request: pending)
        host.play()
        until(lambda: host.snapshot()['transport']['pending'] == 1)
        host.close()
        pending.set_result(dict(result={'late': True}))
        self.assertFalse((self.directory / 'unexpected-response').exists())
        self.assertEqual(host.snapshot()['transport']['pending'], 0)
        self.assertFalse(any(t.name.startswith('agent-ipc-') and t.is_alive() for t in threads()))

    def test_initialization_failure_and_timeout_clean_descendants(self):
        for timeout in (False, True):
            with self.subTest(timeout=timeout):
                self.clear_markers()
                configuration = dict(run_id='run', template=dict(granted_tools=[], limits={}))
                with self.assertRaises(AgentStartupError) as raised:
                    AgentProcessHost(
                        self.submit,
                        configuration,
                        self.limits,
                        agent_factory=FailingFactory(str(self.directory), timeout=timeout),
                        startup_timeout=3,
                        shutdown_grace=0.1,
                    )
                self.assertEqual(
                    raised.exception.code,
                    'AGENT_INITIALIZATION_TIMEOUT' if timeout else 'AGENT_INITIALIZATION_FAILED',
                )
                self.assertNotIn('private user exception', str(raised.exception))
                self.assert_reaped(self.descendants())

    def test_failed_tree_cleanup_retains_ownership_and_can_retry(self):
        host = self.host(cpu_program)
        host.play()
        until(lambda: (self.directory / 'busy').exists())
        with patch.object(
            host._family, 'terminate', side_effect=OSError('temporary cleanup failure')
        ):
            with self.assertRaises(OSError):
                host.close()
        self.assertTrue(host.snapshot()['cleanup_pending'])
        self.assertNotEqual(host.snapshot()['state'], 'closed')
        self.assertTrue(alive(host.snapshot()['pid']))
        host.close()
        self.assert_reaped([host.snapshot()['pid']])
        self.assertFalse(host.snapshot()['cleanup_pending'])

    def test_callback_failure_still_closes_bridge_and_can_retry_notification(self):
        notice = []

        def callback(*args):
            if not notice:
                notice.append('retry')
                raise ContractError('callback temporarily unavailable')
            notice.append(args)

        host = self.host(cpu_program, on_exit=callback)
        host.play()
        until(lambda: (self.directory / 'busy').exists())
        host.close()
        self.assertEqual(len(notice), 2)
        self.assert_reaped([host.snapshot()['pid']])

    def test_transport_fault_stops_agent_and_reports_the_cause(self):
        host = self.host(malformed_program)
        host.play()
        until(lambda: host.snapshot()['state'] == 'failed')
        self.assertEqual(host.snapshot()['exit_reason'], 'transport_error')
        self.assertIsNotNone(host.snapshot()['transport']['error'])
        host.close()
        self.assert_reaped([host.snapshot()['pid']])

    def test_missing_process_family_attachment_never_runs_user_program(self):
        with patch('sim_platform.agents.hosting.host.create_process_family') as factory:
            family = factory.return_value
            family.attach.side_effect = OSError('ownership unavailable')
            # The partial-start fallback must still terminate the gated root process.
            family.terminate.side_effect = lambda process, **kwargs: (
                process.terminate() if process.is_alive() else None
            )
            before = {p.pid for p in multiprocessing.active_children()}
            with self.assertRaises(OSError):
                self.host(cpu_program)
            self.assertFalse((self.directory / 'busy').exists())
            self.assertEqual({p.pid for p in multiprocessing.active_children()}, before)


if __name__ == '__main__':
    unittest.main()
