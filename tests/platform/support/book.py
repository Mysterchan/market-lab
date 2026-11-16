"""Indexed order-book input and reader fixture; callers own registered cleanup."""

from pathlib import Path
from tempfile import TemporaryDirectory
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from itch_fixtures import directory, add, reduce
from sim_platform.market.prepared.builder import build
from sim_platform.market.prepared.reader import PreparedMarket


def fixture(path):
    path.write_bytes(
        directory('AAPL', timestamp=1)
        + add(1, 'B', 20, 99, 'AAPL', timestamp=2)
        + add(2, 'S', 2, 101, 'AAPL', timestamp=3)
        + add(3, 'S', 3, 102, 'AAPL', timestamp=10)
        + add(4, 'S', 4, 103, 'AAPL', timestamp=10)
        + reduce('X', 1, 1, timestamp=20)
        + add(5, 'S', 10, 101, 'AAPL', timestamp=30)
        + b'\0\0'
    )
    return path


class PreparedBookFixture:
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = fixture(self.root / 'source.bin')
        self.destination = self.root / 'prepared'

    def ready(self):
        build(self.source, self.destination)
        reader = PreparedMarket(self.destination)
        self.addCleanup(reader.close)
        return reader
