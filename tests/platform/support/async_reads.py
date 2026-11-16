"""Owned real-process fixture for formatting/cleanup tests; no TestCase lifecycle."""

from functools import partial
import multiprocessing
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from sim_platform import Platform
from support.clock import TestClock as ExperimentClock
from sim_platform.agents.tools.reads.formatter import ReadFormatter
from sim_platform.agents.tools.worker.host import ToolProcessHost
from sim_platform.examples.market_demo import write_fixture
from support.agents import definition, settings
from support.clock import FakeTime


class FileSignal:
    """Spawn-test barrier without a shared lock that termination could poison."""

    def __init__(self, path):
        self.path = Path(path)

    def set(self):
        self.path.touch()

    def clear(self):
        self.path.unlink(missing_ok=True)

    def wait(self, timeout):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if self.path.exists():
                return True
            time.sleep(0.005)
        return False


class BlockingFormatter(ReadFormatter):
    def __init__(self, binding, entered, release):
        super().__init__(binding)
        self.entered, self.release = entered, release

    def execute(self, captured):
        assert multiprocessing.current_process().name == 'agent-tools'
        assert not hasattr(self, 'capture')
        self.entered.set()
        if not self.release.wait(10):
            raise RuntimeError('formatter was not released')
        return super().execute(captured)


class FaultFormatter(ReadFormatter):
    def execute(self, captured):
        raise RuntimeError('query fault')


class AsyncReadFixture:
    def prepare(self, add_cleanup):
        self.temp = TemporaryDirectory()
        add_cleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = Platform(self.root / 'accounts')
        add_cleanup(self.app.close)
        self.profile = self.app.create_profile('Async reads')
        self.account = self.app.create_account(self.profile, 'Cash', 100_000_000)
        self.source, _ = write_fixture(self.root / 'fixture')
        self.app.create_agent(self.profile, definition())
        self.origin = 34_202_500_000_000
        self.now = FakeTime()
        self.config = dict(
            settings(), start_ns=str(self.origin), end_ns=str(self.origin + 4_000_000_000)
        )
        self.entered = FileSignal(self.root / 'entered')
        self.release = FileSignal(self.root / 'release')
        add_cleanup(self.release.set)

    def open(self, factory=None):
        factory = factory or partial(BlockingFormatter, entered=self.entered, release=self.release)
        host = partial(ToolProcessHost, formatter_factory=factory)
        with patch('sim_platform.agents.tools.server.router.ToolProcessHost', host):
            experiment = self.app.open_experiment(
                self.profile,
                self.account,
                source=self.source,
                method='agent',
                agent_id='demo',
                revision=1,
                settings=self.config,
                clock=ExperimentClock(self.origin, self.now),
                execution_mode='controlled',
            )
        experiment.play()
        return experiment
