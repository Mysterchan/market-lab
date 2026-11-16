"""Generate tiny synthetic AAPL BinaryFILE + publication fixture (no Nasdaq data)."""

from __future__ import annotations

import json
from pathlib import Path
import struct
import sys
from typing import Tuple, Union

NS_PER_SECOND = 1_000_000_000
# The fixture starts at the 09:30 open.
OPEN_SECONDS = 9 * 3600 + 30 * 60
# ITCH message header fields: stock locate and tracking number.
STOCK_LOCATE = 7
TRACKING_NUMBER = 0


def write_fixture(directory: Union[str, Path]) -> Tuple[Path, Path]:
    """Write the synthetic AAPL feed and its news file; return their paths."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)

    def common(kind: str, stamp: int) -> bytes:
        return (
            kind.encode()
            + struct.pack('>HH', STOCK_LOCATE, TRACKING_NUMBER)
            + stamp.to_bytes(6, 'big')
        )

    def frame(payload: bytes) -> bytes:
        return struct.pack('>H', len(payload)) + payload

    stock = b'AAPL    '
    second = NS_PER_SECOND
    start = OPEN_SECONDS * second
    records = [
        frame(
            common('R', start)
            + stock
            + b'Q'
            + b'N'
            + struct.pack('>I', 100)
            + b'N'
            + b'C'
            + b'  '
            + b'P'
            + b'N'
            + b'N'
            + b'1'
            + b'N'
            + struct.pack('>I', 1)
            + b'N'
        )
    ]
    for order, side, quantity, price, offset in [
        (1, b'B', 100, 1_900_000, 1),
        (2, b'S', 80, 1_901_000, 2),
    ]:
        records.append(
            frame(
                common('A', start + offset * second)
                + struct.pack('>Q', order)
                + side
                + struct.pack('>I', quantity)
                + stock
                + struct.pack('>I', price)
            )
        )
    records.append(frame(common('E', start + 4 * second) + struct.pack('>QIQ', 1, 25, 101)))
    records.append(
        frame(
            common('P', start + 5 * second)
            + struct.pack('>Q', 0)
            + b'B'
            + struct.pack('>I', 10)
            + stock
            + struct.pack('>IQ', 1_900_500, 102)
        )
    )
    records.append(frame(common('B', start + 6 * second) + struct.pack('>Q', 102)))
    records.append(frame(common('D', start + 7 * second) + struct.pack('>Q', 2)))
    path = directory / 'aapl.bin'
    path.write_bytes(b''.join(records) + b'\0\0')
    news = directory / 'news.json'
    news.write_text(
        json.dumps(
            [
                dict(
                    record_id='earnings-demo',
                    available_time_ns=start + 3 * second,
                    period='2026-Q2',
                    headline='Synthetic earnings announcement',
                )
            ],
            indent=2,
        )
    )
    return path, news


if __name__ == '__main__':
    for path in write_fixture(sys.argv[1] if len(sys.argv) > 1 else 'build/market-demo'):
        print(path)
