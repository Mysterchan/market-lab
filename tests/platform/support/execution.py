"""Owned synthetic account/market fixture for execution and recovery tests."""

from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from itch_fixtures import directory, add, reduce
from sim_platform import Platform

LIBRARY = None


class ExecutionFixture:
    """Use directly with close(), or borrow setUp in a TestCase with addCleanup."""

    def setUp(self):
        self.temp = TemporaryDirectory(prefix='execution-test-')
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.app = Platform(self.path / 'accounts')
        self.addCleanup(self.app.close)
        self.profile = self.app.create_profile('Owner')
        self.account = self.app.create_account(self.profile, 'Account', 100_000_000)
        self.maker = self.app.create_maker(self.profile, 'Human', 'human', 'v1')

    def prefix(self):
        return (
            directory('AAPL', timestamp=1)
            + add(1, 'B', 100, 1_000_000, 'AAPL', timestamp=2)
            + add(2, 'S', 10, 1_001_000, 'AAPL', timestamp=3)
        )

    def scenario(self, tail=None, config=None, cash=None, news=()):
        if tail is None:
            tail = (
                reduce('X', 1, 1, timestamp=4)
                + reduce('X', 1, 1, timestamp=5)
                + add(3, 'S', 5, 1_001_000, 'AAPL', timestamp=6)
            )
        account = (
            self.account if cash is None else self.app.create_account(self.profile, 'Small', cash)
        )
        source = self.path / ('feed-' + str(len(list(self.path.glob('feed-*')))) + '.bin')
        source.write_bytes(self.prefix() + tail + b'\0\0')
        market = self.app.open_market('AAPL', library=getattr(self, "library", LIBRARY))
        self.addCleanup(market.close)
        market.load(source, news=news)
        run = self.app.create_execution_run(self.profile, account, self.maker, 'AAPL', config)
        execution = self.app.execution_session(self.profile, run, self.maker, market)
        self.addCleanup(execution.close)
        for _ in range(3):
            execution.step()
        return execution, market, run, account

    def drain(self, execution):
        execution.play()
        while execution.snapshot.status == 'running':
            execution.pump(10)

    def close(self):
        self._resources.close()

    def __init__(self, *, library=None):
        self._resources = ExitStack()
        self.addCleanup = self._resources.callback
        self.library = library
        try:
            self.setUp()
        except BaseException:
            self.close()
            raise
