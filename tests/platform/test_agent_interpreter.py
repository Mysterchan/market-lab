"""Portable installed-SDK and selected-interpreter host acceptance, with no App/native dependencies."""

from concurrent.futures import Future
import json
import multiprocessing
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
import unittest

from sim_platform.agents.hosting.host import AgentProcessHost
from sim_platform.agents.hosting.loader import ProjectFactory
from sim_platform.agents.hosting.protocol import TransportLimits
from sim_platform.persistence.agents.sdk import SDKFiles

SOURCE = '''import json, sys, time
from pathlib import Path
from sim_platform.sdk import Tool
import sim_platform.sdk
class Double(Tool):
    name, description = 'double', 'Double a number'
    def run(self, value): return value * 2
class Agent:
    def __init__(self, context): self.context = context
    def run(self, task):
        self.context.tool('market.quote').run('AAPL')
        Path(self.context.settings['parameters']['report']).write_text(json.dumps(dict(
            sdk=sim_platform.sdk.__file__, value=Double().run(3), python=sys.version.split()[0])))
        while not self.context.stopping: time.sleep(0.01)
def create_agent(context): return Agent(context)
'''


def until(predicate):
    deadline = time.monotonic() + 10
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError('selected interpreter condition did not complete')
        time.sleep(0.01)


class InstalledInterpreterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.root = Path(cls.temporary.name)
        cls.wheel = SDKFiles(cls.root).wheel()
        cls.environment = cls.root / 'environment'
        subprocess.check_call(
            [sys.executable, '-m', 'venv', str(cls.environment)], stdout=subprocess.DEVNULL
        )
        cls.python = cls.environment / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        subprocess.check_call(
            [
                str(cls.python),
                '-I',
                '-m',
                'pip',
                'install',
                '--no-index',
                '--no-deps',
                '--no-cache-dir',
                '--no-compile',
                str(cls.wheel),
            ],
            stdout=subprocess.DEVNULL,
        )

    def setUp(self):
        self.working = TemporaryDirectory()
        self.addCleanup(self.working.cleanup)
        self.project = Path(self.working.name)
        self.report = self.project / 'report.json'
        (self.project / 'agent.py').write_text(SOURCE)
        self.calls = []

    def submit(self, request):
        self.calls.append(request)
        future = Future()
        future.set_result(dict(result=dict(status='ok')))
        return future

    def work(self):
        future = Future()
        future.set_result(dict(result=dict(work_id='work-1', epoch='1', events=[])))
        return future

    def host(self):
        host = AgentProcessHost(
            self.submit,
            dict(
                run_id='run-1',
                template=dict(
                    initial_prompt='Evaluate',
                    parameters=dict(report=str(self.report)),
                    granted_tools=['market.quote'],
                    symbol='AAPL',
                    limits=dict(
                        tool_request_bytes=4096,
                        tool_response_bytes=4096,
                        pending_requests=4,
                        page_items=100,
                        event_buckets=16,
                        depth_levels=5,
                        json_depth=32,
                    ),
                ),
            ),
            TransportLimits(4096, 4096, 4),
            agent_factory=ProjectFactory(str(self.project)),
            launch=dict(executable=str(self.python), project=str(self.project)),
            request_work=self.work,
            shutdown_grace=0.1,
        )
        self.addCleanup(host.close)
        return host

    def test_installed_sdk_local_tool_and_platform_ipc_without_app_paths(self):
        host = self.host()
        self.assertFalse(self.report.exists())
        host.play()
        until(lambda: self.report.exists() and self.report.stat().st_size > 0)
        report = json.loads(self.report.read_text())
        self.assertEqual(report['value'], 6)
        self.assertIn('site-packages', report['sdk'])
        self.assertEqual(self.calls[0]['tool'], 'market.quote')
        host.close()
        self.assertFalse((self.project / '__pycache__').exists())
        self.assertEqual(host.snapshot()['state'], 'closed')

    def test_selected_interpreter_cpu_loop_still_obeys_host_termination(self):
        source = SOURCE.replace("self.context.tool('market.quote').run('AAPL')", 'while True: pass')
        (self.project / 'agent.py').write_text(source)
        host = self.host()
        host.play()
        time.sleep(0.1)
        started = time.monotonic()
        pid = host.snapshot()['pid']
        host.close()
        self.assertLess(time.monotonic() - started, 3)
        self.assertNotIn(pid, [p.pid for p in multiprocessing.active_children()])


if __name__ == '__main__':
    unittest.main()
