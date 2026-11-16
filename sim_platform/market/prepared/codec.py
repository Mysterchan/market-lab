"""Fixed-width big-endian event index and bounded-depth market versions.

Only a small manifest is JSON. No Python book, per-query replay or SQL storage.
"""

from __future__ import annotations

from struct import Struct
from typing import Any, Sequence

from sim_platform.domain.models import ContractError
from sim_platform.market.models import MarketEvent, MarketSnapshot, MarketTrade

# time, sequence, state version, kind, source, trade id/price/size, flags, broken id
EVENT = Struct('>QQQccQQQBQ')
# Book time (-1 if none), total bid levels, total ask levels, trading state,
# system event, retained bid count, retained ask count. Levels follow.
STATE = Struct('>qQQ16s16sHH')
# Bytes of a state record that hold the book time and total level counts. Stable
# spans ignore them, because they do not change the liquidity a simulated order sees.
STATE_HEADER_PREFIX = Struct('>qQQ').size
# One price level: price, displayed shares.
LEVEL = Struct('>QQ')
KINDS = {'market': b'M', 'clock': b'C'}
# Event flag bits.
HAS_TRADE, PRINTABLE, BROKEN = 1, 2, 4


def state_size(depth: int) -> int:
    """Bytes of one state record that keeps ``depth`` levels per side."""
    return STATE.size + 2 * depth * LEVEL.size


def encode_state(snapshot: MarketSnapshot, depth: int) -> bytes:
    """Pack a snapshot's book and permissions into a fixed-size state record."""
    trading = snapshot.trading_state.encode('ascii')
    system = snapshot.system_event.encode('ascii')
    if len(trading) > 16 or len(system) > 16:
        raise ContractError('unsupported prepared market state')
    raw = STATE.pack(
        -1 if snapshot.book_time_ns is None else snapshot.book_time_ns,
        snapshot.bid_levels,
        snapshot.ask_levels,
        trading,
        system,
        len(snapshot.bids),
        len(snapshot.asks),
    )
    for levels in (snapshot.bids, snapshot.asks):
        if len(levels) > depth:
            raise ContractError('prepared depth exceeded')
        raw += b''.join(LEVEL.pack(*level) for level in levels)
        raw += bytes((depth - len(levels)) * LEVEL.size)
    return raw


def decode_state(
    raw: Any, symbol: str, stamp: int, sequence: int, depth: int, status: str = 'playing'
) -> MarketSnapshot:
    """Rebuild a snapshot from a state record, rejecting any inconsistent content."""
    book_time, bids_total, asks_total, trading, system, nb, na = STATE.unpack_from(raw)
    if (
        nb > depth
        or na > depth
        or bids_total < nb
        or asks_total < na
        or book_time < -1
        or book_time > stamp
    ):
        raise ContractError('invalid prepared state')
    sides = []
    for side, count in enumerate((nb, na)):
        offset = STATE.size + side * depth * LEVEL.size
        values = tuple(LEVEL.unpack_from(raw, offset + i * LEVEL.size) for i in range(count))
        if any(not price or not quantity for price, quantity in values):
            raise ContractError('invalid prepared level')
        prices = [price for price, _ in values]
        if len(set(prices)) != len(prices) or prices != sorted(prices, reverse=side == 0):
            raise ContractError('unordered prepared levels')
        sides.append(values)
    return MarketSnapshot(
        symbol,
        stamp,
        sequence,
        status,
        'ordered_unverified',
        sides[0],
        sides[1],
        bids_total,
        asks_total,
        None if book_time == -1 else book_time,
        trading.rstrip(b'\0').decode('ascii'),
        system.rstrip(b'\0').decode('ascii'),
    )


def encode_event(event: MarketEvent, version: int) -> bytes:
    """Pack an event; ``version`` is the index of the state record it leaves behind."""
    trade = event.trade
    flags = 0
    if trade is not None:
        flags |= HAS_TRADE
        if trade.printable:
            flags |= PRINTABLE
    if event.broken_match_id is not None:
        flags |= BROKEN
    return EVENT.pack(
        event.time_ns,
        event.sequence,
        version,
        KINDS[event.kind],
        event.source_type.encode('ascii'),
        trade.match_id if trade else 0,
        trade.price if trade else 0,
        trade.quantity if trade else 0,
        flags,
        event.broken_match_id if event.broken_match_id is not None else 0,
    )


def decode_event(row: Sequence[Any], snapshot: MarketSnapshot) -> MarketEvent:
    """Rebuild an event from its unpacked record and the snapshot it produced."""
    stamp, _, _, kind, source, match, price, quantity, flags, broken = row
    sequence = snapshot.sequence
    source = source.decode('ascii')
    trade = (
        MarketTrade(sequence, stamp, match, price, quantity, bool(flags & PRINTABLE), source)
        if flags & HAS_TRADE
        else None
    )
    return MarketEvent(
        'market' if kind == b'M' else 'clock',
        stamp,
        sequence,
        source,
        snapshot,
        trade=trade,
        broken_match_id=broken if flags & BROKEN else None,
    )
