"""Value-only tools IPC; no pickle or capability/object import by name.

Encoding runs on the transport adapter, decoding on the child. Only the listed
immutable domain values cross; limits apply before decoding received frames.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import fields, is_dataclass, replace
import json
from base64 import b64encode, b64decode
from struct import Struct
from types import MappingProxyType
from typing import Any, Dict, Tuple, Union

from sim_platform.domain.models import AccountSnapshot, Position, OrderSnapshot, ContractError
from sim_platform.execution.models import ExecutionConfig, ExecutionSnapshot
from sim_platform.market.models import MarketSnapshot, MarketTrade, PriceVolumeBar
from sim_platform.market.window import HistoryCapture
from ..reads.capture import CapturedRead
from ..trading.financial import FinancialCapture

# Largest encoded tools job (2 MiB).
MAX_JOB_BYTES = 2 * 1024 * 1024
# History rows a capture may carry.
MAX_HISTORY_TRADES = 1000
MAX_HISTORY_BARS = 100
# Most orders shipped for one read.
MAX_ORDERS = 1000
MAX_INT64 = 2**63 - 1
VALUE_TYPES = {
    value.__name__: value
    for value in (
        CapturedRead,
        FinancialCapture,
        AccountSnapshot,
        Position,
        OrderSnapshot,
        ExecutionConfig,
        ExecutionSnapshot,
        MarketSnapshot,
        MarketTrade,
        PriceVolumeBar,
        HistoryCapture,
    )
}


TRADE_ROW = Struct('>QQQQQBBQ')
BAR_ROW = Struct('>QQQQQQQQ')


def encode_trade_row(row: Tuple[Any, ...]) -> bytes:
    if len(row[6]) != 1:
        raise ContractError('history trade kind must be one byte')
    return TRADE_ROW.pack(*row[:6], row[6][0], row[7])


def encode_history(history: HistoryCapture) -> Dict[str, Any]:
    """Fixed integer rows as base64 bytes, avoiding thousands of tracked lists.

    This is an encoding, not a hash. Big endian widths preserve native integer
    precision. Only the bounded 1000 trades/100 bars window is supported.
    """
    if len(history.trades) > MAX_HISTORY_TRADES or len(history.bars) > MAX_HISTORY_BARS:
        raise ContractError('history exceeds tools capture bounds')
    trades = b''.join(encode_trade_row(row) for row in history.trades)
    bars = b''.join(BAR_ROW.pack(*row) for row in history.bars)
    return dict(
        cutoff=history.cutoff,
        trades=b64encode(trades).decode('ascii'),
        bars=b64encode(bars).decode('ascii'),
        trade_count=history.trade_count,
        bar_count=history.bar_count,
    )


def decode_history(data: Dict[str, Any]) -> HistoryCapture:
    if set(data) != {'cutoff', 'trades', 'bars', 'trade_count', 'bar_count'}:
        raise ContractError('invalid history capture fields')
    trades = b64decode(data['trades'], validate=True)
    bars = b64decode(data['bars'], validate=True)
    if (
        len(trades) > MAX_HISTORY_TRADES * TRADE_ROW.size
        or len(trades) % TRADE_ROW.size
        or len(bars) > MAX_HISTORY_BARS * BAR_ROW.size
        or len(bars) % BAR_ROW.size
    ):
        raise ContractError('invalid bounded history rows')
    rows = tuple((*row[:6], bytes((row[6],)), row[7]) for row in TRADE_ROW.iter_unpack(trades))
    return HistoryCapture(
        data['cutoff'],
        rows,
        tuple(BAR_ROW.iter_unpack(bars)),
        data['trade_count'],
        data['bar_count'],
    )


def pack_value(value: Any) -> Any:
    """Encode an allowed value as JSON: plain values, bytes, listed dataclasses, maps, tuples."""
    if type(value) in (str, int, bool, float) or value is None:
        return value
    if type(value) is bytes:
        return dict(type='bytes', value=value.hex())
    if isinstance(value, HistoryCapture):
        return dict(type='HistoryCapture', value=encode_history(value))
    if (
        is_dataclass(value)
        and type(value).__name__ in VALUE_TYPES
        and VALUE_TYPES[type(value).__name__] is type(value)
    ):
        return dict(
            type=type(value).__name__,
            value={field.name: pack_value(getattr(value, field.name)) for field in fields(value)},
        )
    if isinstance(value, Mapping):
        return dict(type='map', value={key: pack_value(child) for key, child in value.items()})
    if isinstance(value, (tuple, list)):
        return dict(type='tuple', value=[pack_value(child) for child in value])
    raise ContractError('unsupported tools IPC value: ' + type(value).__name__)


def unpack_value(value: Any) -> Any:
    if type(value) is not dict:
        return value
    if set(value) != {'type', 'value'}:
        raise ContractError('invalid tools IPC value')
    kind, data = value['type'], value['value']
    if kind == 'map':
        return {key: unpack_value(child) for key, child in data.items()}
    if kind == 'tuple':
        return tuple(unpack_value(child) for child in data)
    if kind == 'bytes':
        return bytes.fromhex(data)
    if kind == 'HistoryCapture':
        return decode_history(data)
    value_type = VALUE_TYPES.get(kind)
    if value_type is None or set(data) != {field.name for field in fields(value_type)}:
        raise ContractError('unknown tools IPC value type or fields')
    return value_type(**{key: unpack_value(child) for key, child in data.items()})


def project_read(captured: CapturedRead) -> CapturedRead:
    """Transport-only selection; the owner has already pinned immutable values.

    Keep only requested depth/orders. Quote/history need account sequence, not
    the full account. Large owner state is never sent to every tool.
    """
    tool, args = captured.request['tool'], captured.request['arguments']
    account = captured.execution.account
    orders = account.orders if tool in ('orders.list', 'orders.get', 'state.snapshot') else ()
    if tool == 'orders.get':
        orders = tuple(value for value in orders if value.order_id == args['order_id'])
    # Keep the existing explicit >1000-orders error, without shipping all rows.
    if len(orders) > MAX_ORDERS:
        from .contracts import ToolError

        raise ToolError('DATA_UNAVAILABLE', 'order history exceeds the supported window')
    account_needed = tool in ('account.snapshot', 'state.snapshot')
    selected = replace(
        account, orders=orders, positions=account.positions if account_needed else ()
    )
    execution = replace(
        captured.execution,
        account=selected,
        reserved_shares=captured.execution.reserved_shares if account_needed else (),
    )
    levels = (
        args.get('levels', 1) if tool in ('market.depth', 'market.quote', 'state.snapshot') else 0
    )
    market = replace(
        captured.market, bids=captured.market.bids[:levels], asks=captured.market.asks[:levels]
    )
    versions = {value.order_id: captured.order_versions[value.order_id] for value in orders}
    return replace(
        captured, market=market, execution=execution, order_versions=MappingProxyType(versions)
    )


def encode_job(identity: int, captured: Union[CapturedRead, FinancialCapture]) -> bytes:
    if isinstance(captured, CapturedRead):
        captured = project_read(captured)
    message = dict(identity=identity, capture=pack_value(captured))
    raw = json.dumps(message, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode()
    if len(raw) > MAX_JOB_BYTES:
        from .contracts import ToolError

        raise ToolError('OUTPUT_TOO_LARGE', 'tools snapshot exceeds IPC byte limit')
    return raw


def decode_job(raw: bytes) -> Tuple[int, Union[CapturedRead, FinancialCapture]]:
    if len(raw) > MAX_JOB_BYTES:
        raise ContractError('tools job exceeds IPC byte limit')
    message = json.loads(raw)
    if (
        set(message) != {'identity', 'capture'}
        or type(message['identity']) is not int
        or not 1 <= message['identity'] <= MAX_INT64
    ):
        raise ContractError('invalid tools job envelope')
    capture = unpack_value(message['capture'])
    if not isinstance(capture, (CapturedRead, FinancialCapture)):
        raise ContractError('invalid tools capture type')
    return message['identity'], capture
