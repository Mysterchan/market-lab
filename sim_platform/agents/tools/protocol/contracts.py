"""Strict tool request validation, before any capability is accessed."""

from __future__ import annotations

from sim_platform.agents.configuration import validate_identifier
import json
from typing import Any, Dict, List, Tuple
from sim_platform.domain.json_values import encode
from sim_platform.domain.models import ContractError

from .errors import ToolError
from ..client.collection import TOOL_TYPES

READ_TOOLS = frozenset(name for name, tool in TOOL_TYPES.items() if tool.category == 'read')
TRADE_TOOLS = frozenset(name for name, tool in TOOL_TYPES.items() if tool.category == 'trade')
CONTROL_TOOLS = frozenset(name for name, tool in TOOL_TYPES.items() if tool.category == 'control')
IMPLEMENTED_TOOLS = frozenset(TOOL_TYPES)
# Bounds of one fundamentals.get read (market.fundamentals).
MAX_FUNDAMENTAL_PERIODS = 20
MAX_FUNDAMENTAL_METRICS = 50
MAX_DOCUMENT_RESULTS = 20
MAX_JOURNAL_CHARACTERS = 4000
MARKET_TOOLS = frozenset(
    name for name, tool in TOOL_TYPES.items() if tool.execution_route == 'market'
)
# Largest signed 64-bit integer: share and money bounds.
MAX_INT64 = 2**63 - 1
# Upper bound of a market time: 100 years of nanoseconds from the recording's first
# midnight. Same value as market.models.MAX_TIME_NS, which this protocol module does
# not import.
MAX_TIME_NS = 36_525 * 86_400_000_000_000
# Most digits in a counter, and money units per USD.
COUNTER_MAX_DIGITS = 20
PRICE_SCALE = 10000
# Longest price text and most decimal places in a price.
PRICE_TEXT_MAX_LENGTH = 24
PRICE_DECIMALS = 4

# A tool request: 'run_id', 'work_id', 'epoch', 'request_id', 'tool' and 'arguments'.
ToolRequest = Dict[str, Any]
# The run's tool binding: 'run_id', 'work_id', 'epoch', 'symbol', 'granted_tools',
# 'limits' and the source references.
ToolBinding = Dict[str, Any]

ARGUMENTS = {name: tool.required_arguments() for name, tool in TOOL_TYPES.items()}
OPTIONAL_ARGUMENTS = {name: tool.optional_arguments() for name, tool in TOOL_TYPES.items()}


def counter(value: Any) -> int:
    """Parse a canonical decimal time string within the session day."""
    if (
        not isinstance(value, str)
        or not value.isascii()
        or not value.isdecimal()
        or len(value) > COUNTER_MAX_DIGITS
        or (len(value) > 1 and value.startswith('0'))
    ):
        raise ToolError('INVALID_ARGUMENT', 'expected canonical decimal time')
    result = int(value)
    if result >= MAX_TIME_NS:
        raise ToolError('INVALID_ARGUMENT', 'time outside the supported market time range')
    return result


def bounded(value: Any, low: int, high: int) -> None:
    if type(value) is not int or not low <= value <= high:
        raise ToolError('INVALID_ARGUMENT', 'integer outside supported range')


def identifier(value: Any) -> None:
    try:
        validate_identifier(value)
    except ContractError as exc:
        raise ToolError('INVALID_SCHEMA', 'invalid identifier') from exc


def price_units(value: Any) -> int:
    """Parse plain decimal USD exactly; no float, exponent or rounding."""
    if (
        not isinstance(value, str)
        or len(value) > PRICE_TEXT_MAX_LENGTH
        or not value.isascii()
        or value.count('.') > 1
    ):
        raise ToolError('INVALID_ARGUMENT', 'expected decimal USD string')
    parts = value.split('.')
    if not parts[0].isdigit() or (
        len(parts) > 1 and (not parts[1].isdigit() or len(parts[1]) > PRICE_DECIMALS)
    ):
        raise ToolError('INVALID_ARGUMENT', 'unsupported USD precision or syntax')
    units = int(parts[0]) * PRICE_SCALE + (
        int(parts[1].ljust(PRICE_DECIMALS, '0')) if len(parts) > 1 else 0
    )
    if not 1 <= units <= MAX_INT64:
        raise ToolError('INVALID_ARGUMENT', 'price outside supported range')
    return units


def validate_depth(value: Any, maximum: int) -> None:
    """Raise if JSON containers nest deeper than ``maximum``."""
    pending: List[Tuple[Any, int]] = [(value, 0)]
    while pending:
        item, depth = pending.pop()
        if depth > maximum:
            raise ToolError('INVALID_SCHEMA', 'request exceeds JSON depth limit')
        if isinstance(item, dict):
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            pending.extend((child, depth + 1) for child in item)


def decode_request(value: Any, limits: Dict[str, Any]) -> Any:
    """Detach finite JSON; enforce request UTF-8 bytes and nesting before field access."""
    try:
        encoded = encode(value)
    except (ContractError, RecursionError) as exc:
        raise ToolError('INVALID_JSON', 'request must be finite JSON') from exc
    if len(encoded.encode()) > limits['tool_request_bytes']:
        raise ToolError('INVALID_SCHEMA', 'request exceeds byte limit')
    request = json.loads(encoded)
    validate_depth(request, limits['json_depth'])
    return request


def validate_envelope(request: Any) -> None:
    """Require the tool envelope fields and valid run/work/request identifiers."""
    fields = {'run_id', 'work_id', 'epoch', 'request_id', 'tool', 'arguments'}
    if type(request) is not dict or set(request) != fields:
        raise ToolError('INVALID_SCHEMA', 'invalid tool envelope')
    for name in ('run_id', 'work_id', 'request_id'):
        identifier(request[name])


def validate_work_identity(request: ToolRequest, binding: ToolBinding) -> None:
    """Reject another run, superseded work or stale epoch; no capability is accessed."""
    fields = ('run_id', 'work_id', 'epoch')
    if tuple(request[name] for name in fields) != tuple(binding[name] for name in fields):
        raise ToolError('STALE_WORK', 'request is not bound to this work')


def validate_tool_permission(tool: Any, binding: ToolBinding) -> None:
    """Require an implemented tool and an explicit grant; describe is always allowed."""
    if not isinstance(tool, str) or tool not in IMPLEMENTED_TOOLS:
        raise ToolError('TOOL_UNAVAILABLE', 'tool is not implemented')
    if tool != 'environment.describe' and tool not in binding['granted_tools']:
        raise ToolError('FORBIDDEN', 'tool is not granted')


def validate_argument_fields(tool: str, arguments: Any) -> None:
    """Check exact required fields; only trades/bars/orders.list may add a cursor."""
    optional = set(OPTIONAL_ARGUMENTS[tool])
    required = set(ARGUMENTS[tool])
    if (
        type(arguments) is not dict
        or not required <= set(arguments)
        or set(arguments) - required - optional
    ):
        raise ToolError('INVALID_SCHEMA', 'invalid arguments for ' + tool)


def binding_universe(binding: ToolBinding) -> List[str]:
    """Symbols a tool may name: the run's ``symbols``, or its one ``symbol``."""
    return list(binding.get('symbols') or [binding['symbol']])


def validate_common_arguments(arguments: Dict[str, Any], binding: ToolBinding) -> None:
    """Check experiment symbol, page/depth bounds and identifier syntax, not availability."""
    if 'symbol' in arguments and arguments['symbol'] not in binding_universe(binding):
        raise ToolError('INVALID_ARGUMENT', 'symbol differs from the experiment')
    bounds = {
        'limit': (1, binding['limits']['page_items']),
        'order_limit': (0, binding['limits']['page_items']),
        'levels': (1, binding['limits']['depth_levels']),
    }
    for name, (low, high) in bounds.items():
        if name in arguments:
            bounded(arguments[name], low, high)
    for name in ('cursor', 'order_id', 'target_request_id'):
        if name in arguments:
            identifier(arguments[name])


def validate_bar_arguments(arguments: Dict[str, Any]) -> None:
    """Support 60s bars and a nonempty same-day range; cutoff checked at execution."""
    if arguments['interval_seconds'] != '60':
        raise ToolError('INVALID_ARGUMENT', 'only one-minute bars are supported')
    if counter(arguments['start_ns']) >= counter(arguments['end_ns']):
        raise ToolError('INVALID_ARGUMENT', 'empty or reversed bar range')


def validate_limit_order_arguments(arguments: Dict[str, Any]) -> None:
    """Require whole positive shares, buy/sell, exact USD and int64 notional.

    Available funds/shares and market eligibility belong to execution risk.
    """
    bounded(arguments['quantity_shares'], 1, MAX_INT64)
    if arguments['side'] not in ('buy', 'sell'):
        raise ToolError('INVALID_ARGUMENT', 'unsupported order side')
    price = price_units(arguments['limit_price_usd'])
    if arguments['quantity_shares'] * price > MAX_INT64:
        raise ToolError('INVALID_ARGUMENT', 'notional outside supported range')


def validate_wait_conditions(conditions: Any, symbol: Any) -> None:
    """Translate shared scheduling validation failures into tool wire errors."""
    from sim_platform.experiments.scheduling.policy.conditions import (
        validate_wait_conditions as validate,
    )
    from sim_platform.experiments.scheduling.errors import WorkError

    try:
        validate(conditions, symbol)
    except WorkError as error:
        raise ToolError(error.code, str(error)) from error


def validate_tool_arguments(tool: str, arguments: Dict[str, Any]) -> None:
    """Apply the remaining tool-specific rules after shared fields have passed."""
    if tool == 'orders.list' and arguments['status'] not in ('open', 'all'):
        raise ToolError('INVALID_ARGUMENT', 'unsupported order status filter')
    if tool == 'market.bars':
        validate_bar_arguments(arguments)
    elif tool == 'orders.submit_limit':
        validate_limit_order_arguments(arguments)
    elif tool == 'fundamentals.get':
        bounded(arguments['periods'], 1, MAX_FUNDAMENTAL_PERIODS)
        metrics = arguments.get('metrics')
        if metrics is not None and (
            type(metrics) is not list
            or not 1 <= len(metrics) <= MAX_FUNDAMENTAL_METRICS
            or any(type(m) is not str or not 1 <= len(m) <= 128 for m in metrics)
        ):
            raise ToolError('INVALID_ARGUMENT', 'metrics must list 1 to 50 metric names')
    elif tool == 'documents.search':
        bounded(arguments['limit'], 1, MAX_DOCUMENT_RESULTS)
        if type(arguments['query']) is not str or len(arguments['query']) > 500:
            raise ToolError('INVALID_ARGUMENT', 'query must be text of at most 500 characters')
    elif tool == 'documents.read':
        bounded(arguments.get('offset', 0), 0, MAX_INT64)
        document = arguments['document_id']
        if type(document) is not str or not 1 <= len(document) <= 200:
            raise ToolError('INVALID_ARGUMENT', 'document_id must be text')
    elif tool == 'journal.write':
        text = arguments['text']
        if type(text) is not str or not 1 <= len(text) <= MAX_JOURNAL_CHARACTERS:
            raise ToolError('INVALID_ARGUMENT', 'text must be 1 to 4000 characters')
    elif tool == 'orders.submit_market':
        bounded(arguments['quantity_shares'], 1, MAX_INT64)
        if arguments['side'] not in ('buy', 'sell'):
            raise ToolError('INVALID_ARGUMENT', 'unsupported order side')


def validate_request(value: Any, binding: ToolBinding, *, check_work: bool = True) -> ToolRequest:
    """Ingress-only pipeline: JSON → envelope → identity → grant → arguments.

    Return detached JSON. Never capture market/account, query storage or reserve
    funds. Availability, cutoff, request conflicts and finance are execution checks.
    """
    request = decode_request(value, binding['limits'])
    validate_envelope(request)
    if check_work:
        validate_work_identity(request, binding)
    elif request['run_id'] != binding['run_id']:
        raise ToolError('STALE_WORK', 'request belongs to another run')
    validate_tool_permission(request['tool'], binding)
    validate_argument_fields(request['tool'], request['arguments'])
    validate_common_arguments(request['arguments'], binding)
    validate_tool_arguments(request['tool'], request['arguments'])
    if request['tool'] == 'control.wait':
        validate_wait_conditions(request['arguments']['conditions'], binding_universe(binding))
    return request
