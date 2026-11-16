"""Tool wire envelopes and UTF-8 budgets, shared by read and financial handlers."""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

from sim_platform.domain.json_values import copy_json, encode
from .contracts import ToolError

# Digits reserved for an unknown counter, and bytes for an unknown rejection code.
COUNTER_BUDGET = 20
REASON_CODE_BUDGET = 160
# Execution order IDs append ':order:' and a 64-character digest to the run ID.
ORDER_ID_SUFFIX_LENGTH = 7 + 64

# A tool response envelope: request identity fields, 'receipt_event_index', 'status'
# and either 'cutoff'/'data' or 'position'/'error'.
ToolResponse = Dict[str, Any]


def response_size(response: Any, maximum: int) -> int:
    """Reject oversized output; financial callers must budget before committing."""
    size = len(encode(response).encode('utf-8'))
    if size > maximum:
        raise ToolError('OUTPUT_TOO_LARGE', 'response exceeds configured byte limit')
    return size


def response_identity(request: Dict[str, Any], receipt_index: str) -> Dict[str, Any]:
    fields = ('run_id', 'work_id', 'epoch', 'request_id', 'tool')
    return dict({name: request[name] for name in fields}, receipt_event_index=receipt_index)


def cutoff(position: Any, account_sequence: Optional[Any], source_id: str) -> Dict[str, Any]:
    """The data cutoff reported with a result: market position, account sequence, source."""
    return dict(
        market_position=copy_json(position),
        account_sequence=None if account_sequence is None else str(account_sequence),
        source_id=source_id,
    )


def success_response(
    request: Dict[str, Any], receipt_index: str, observed_cutoff: Any, data: Any
) -> ToolResponse:
    identity = response_identity(request, receipt_index)
    return dict(identity, status='ok', cutoff=copy_json(observed_cutoff), data=copy_json(data))


def error_response(
    request: Dict[str, Any], receipt_index: str, position: Any, error: ToolError
) -> ToolResponse:
    retry = (
        'correct_input'
        if error.code
        in ('INVALID_ARGUMENT', 'INVALID_SCHEMA', 'FUTURE_DATA', 'WORK_HAS_PENDING_OPERATIONS')
        else 'none'
    )
    if error.code == 'QUEUE_FULL':
        retry = 'retry_same_request'
    return dict(
        response_identity(request, receipt_index),
        status='error',
        position=copy_json(position),
        error=dict(code=error.code, message=str(error), retry_action=retry, details={}),
    )


def object_size(fields: Mapping[str, Any]) -> int:
    """JSON object upper bound from independent field maxima, including punctuation."""
    return (
        2
        + max(0, len(fields) - 1)
        + sum(
            len(encode(name).encode()) + 1 + len(encode(value).encode())
            for name, value in fields.items()
        )
    )


def financial_response_bound(
    request: Dict[str, Any],
    position: Any,
    source_id: str,
    symbol: str,
    quantity: int,
    side: str,
    price_usd: str,
) -> int:
    """Precommit UTF-8 bound, not a fabricated order or an actual response.

    Counters reserve 20 decimal digits. Existing execution order IDs append
    ':order:' plus a 64-character digest to run_id. Rejection codes retain the
    prior 160-byte allowance. Filled/remaining counts are bounded independently.
    """
    envelope = success_response(
        request,
        '9' * COUNTER_BUDGET,
        cutoff(position, '9' * COUNTER_BUDGET, source_id),
        {},
    )
    order_bytes = object_size(
        dict(
            order_id='x' * (len(request['run_id']) + ORDER_ID_SUFFIX_LENGTH),
            symbol=symbol,
            side=side,
            quantity_shares=quantity,
            filled_shares=quantity,
            remaining_shares=quantity,
            limit_price_usd=price_usd,
            status='cancelled',
            version='9' * COUNTER_BUDGET,
        )
    )
    # Replace the empty data object with outcome/order/reason fields. Use null
    # temporarily for the nested order's size, without manufacturing wire data.
    data_bytes = object_size(
        dict(outcome='already_terminal', order=None, reason_code='x' * REASON_CODE_BUDGET)
    )
    return len(encode(envelope).encode()) - 2 + data_bytes - len('null') + order_bytes
