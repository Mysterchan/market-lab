"""Synthetic trading source and positioned request envelopes."""

import struct

ORIGIN = 34_200_000_000_000
SECOND = 1_000_000_000


def fixture(path, quantity=2, price=1_000_000, disappear=False):
    def common(kind, offset):
        return (
            kind.encode() + struct.pack('>HH', 7, 0) + (ORIGIN + offset * SECOND).to_bytes(6, 'big')
        )

    def frame(value):
        return struct.pack('>H', len(value)) + value

    stock = b'AAPL    '
    directory = (
        common('R', 0)
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
    values = [frame(directory)]
    for number, side, qty, cost, offset in [
        (1, b'B', 100, 990000, 1),
        (2, b'S', quantity, price, 2),
    ]:
        values.append(
            frame(
                common('A', offset)
                + struct.pack('>Q', number)
                + side
                + struct.pack('>I', qty)
                + stock
                + struct.pack('>I', cost)
            )
        )
    for offset in range(3, 9):
        if offset == 3 and disappear:
            values.append(frame(common('D', offset) + struct.pack('>Q', 2)))
        else:
            values.append(
                frame(
                    common('A', offset)
                    + struct.pack('>Q', offset)
                    + b'B'
                    + struct.pack('>I', 1)
                    + stock
                    + struct.pack('>I', 980000)
                )
            )
    path.write_bytes(b''.join(values) + b'\0\0')
    return path


def wire(config, tool, args, name):
    return dict(
        run_id=config['run_id'],
        work_id='work-1',
        epoch='1',
        request_id=name,
        tool=tool,
        arguments=args,
    )
