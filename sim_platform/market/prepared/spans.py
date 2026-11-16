"""Import-time certificates for consecutive, financially unchanged book events.

A span has equal retained bids/asks and market permissions, and no trade,
correction, news or control event. It does not authorize skipping an order that
can cross: that run-specific proof remains in ExecutionState.
"""

from __future__ import annotations

import mmap
from pathlib import Path
from struct import Struct
from typing import Any, Callable

from sim_platform.domain.models import ContractError

from .codec import EVENT, STATE_HEADER_PREFIX, state_size

# One record per span: the index just past the span's last event.
SPAN = Struct('>Q')
# ITCH messages that change the displayed book: Add (A, F), Executed (E, C),
# Cancel (X), Delete (D) and Replace (U).
# A requote (market.models.REQUOTE) is deliberately absent: it renews liquidity even
# when the displayed book looks the same, so it must never be skipped as part of a span.
BOOK_EVENTS = frozenset((b'A', b'F', b'E', b'C', b'X', b'D', b'U'))
# Cancellation is checked once per this many events.
_CANCEL_CHECK_EVENTS = 8192


def build_spans(stage: Path, frames: int, depth: int, cancelled: Callable[[], bool]) -> int:
    """Write ``spans.bin`` for a staged artifact and return the number of spans.

    Consecutive events belong to one span while each is a book message without a
    trade and leaves the retained book and trading permissions unchanged.
    """
    size = state_size(depth)
    previous = None
    count = 0
    with (
        (stage / 'events.bin').open('rb') as events_file,
        (stage / 'states.bin').open('rb') as states_file,
    ):
        with (
            mmap.mmap(events_file.fileno(), 0, access=mmap.ACCESS_READ) as events,
            mmap.mmap(states_file.fileno(), 0, access=mmap.ACCESS_READ) as states,
            (stage / 'spans.bin').open('wb') as output,
        ):
            for index in range(frames):
                if index % _CANCEL_CHECK_EVENTS == 0 and cancelled():
                    from .builder import PreparationCancelled

                    raise PreparationCancelled('span preparation cancelled')
                row = EVENT.unpack_from(events, index * EVENT.size)
                # Exclude source time and total depth counts. Neither changes
                # the retained liquidity budget. Permissions and every retained
                # price/quantity are included in the certificate.
                offset = row[2] * size
                key = (
                    states[offset + STATE_HEADER_PREFIX : offset + size]
                    if row[4] in BOOK_EVENTS and not row[8]
                    else None
                )
                if index and (key is None or previous is None or key != previous):
                    output.write(SPAN.pack(index))
                    count += 1
                previous = key
            output.write(SPAN.pack(frames))
    return count + 1


class StableSpans:
    """Read access to the spans of a prepared artifact."""

    def __init__(self, mapping: Any, count: int) -> None:
        self.mapping, self.count = mapping, count

    def end(self, index: int) -> int:
        """Return the index just past the span that contains event ``index``."""
        low, high = 0, self.count
        while low < high:
            mid = (low + high) // 2
            if SPAN.unpack_from(self.mapping, mid * SPAN.size)[0] <= index:
                low = mid + 1
            else:
                high = mid
        if low == self.count:
            raise ContractError('span index outside source')
        return SPAN.unpack_from(self.mapping, low * SPAN.size)[0]

    def validate(self, frames: int) -> None:
        """Check that the spans are increasing and together cover every event."""
        previous = 0
        for index in range(self.count):
            end = SPAN.unpack_from(self.mapping, index * SPAN.size)[0]
            if not previous < end <= frames:
                raise ContractError('invalid stable span ordering')
            previous = end
        if previous != frames:
            raise ContractError('stable spans do not cover source')
