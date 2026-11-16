"""Shared prepared-market input and temporary-store setup.

PreparedMarketFixture is a fixture mixin: its caller supplies addCleanup.
It defines no test cases and registers temporary storage before writing the input.
"""

from pathlib import Path
from tempfile import TemporaryDirectory
import struct
import sys

# The same byte builders serve the standalone native and platform suites.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from itch_fixtures import directory, add, common, frame
from sim_platform.persistence.prepared.store import PreparedStore


def trade(match, price, quantity, stamp):
    return frame(
        common('P', timestamp=stamp)
        + struct.pack('>Q', 100 + match)
        + b'B'
        + struct.pack('>I', quantity)
        + b'AAPL    '
        + struct.pack('>I', price)
        + struct.pack('>Q', match)
    )


class PreparedMarketFixture:
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source.bin'
        self.source.write_bytes(
            directory('AAPL', timestamp=1)
            + add(1, 'B', 20, 99, 'AAPL', timestamp=2)
            + add(2, 'S', 20, 101, 'AAPL', timestamp=3)
            + trade(1, 100, 2, 10)
            + trade(2, 110, 3, 20)
            + frame(common('B', timestamp=30) + struct.pack('>Q', 1))
            + trade(3, 90, 1, 60_000_000_000)
            + b'\0\0'
        )
        self.store = PreparedStore(self.root / 'workspace')
