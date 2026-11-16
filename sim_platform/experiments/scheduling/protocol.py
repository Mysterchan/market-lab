"""Trusted runtime transport; finite JSON, exact fields and byte bounds.

This internal capability is never exposed wholesale to uploaded programs.
Lifecycle adapters grant selected commands and record control barriers.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, FrozenSet, List, NoReturn, Set, Tuple

from sim_platform.domain.json_values import copy_json, encode
from sim_platform.domain.models import ContractError

# Largest encoded message on a Runtime pipe (1 MiB).
MAX_BYTES = 1_048_576
COMMANDS: FrozenSet[str] = frozenset(
    (
        'next_work',
        'finish',
        'wait',
        'observe',
        'admit',
        'deliver',
        'begin_attempt',
        'receive_attempt',
        'model_receipt',
        'agent_failed',
        'inspect',
        'control',
        'resync',
    )
)
FIELDS: Dict[str, Set[str]] = {
    'control': {'control_id', 'control_version', 'action'},
    'resync': {'event_sequence', 'observation', 'events'},
    'next_work': set(),
    'inspect': set(),
    'finish': {'work_id', 'epoch'},
    'wait': {'work_id', 'epoch', 'request_id', 'conditions'},
    'admit': {'work_id', 'epoch', 'request_id'},
    'deliver': {'request_id'},
    'begin_attempt': {'work_id', 'epoch'},
    'agent_failed': {'reason'},
    'observe': {'event_sequence', 'observation', 'events'},
    'receive_attempt': {'attempt_id'},
    'model_receipt': {'attempt_id', 'received_ns'},
}
# Longest work, request, epoch or attempt identity, and its allowed characters.
IDENTITY_MAX_LENGTH = 128
IDENTITY_PATTERN = r'[A-Za-z0-9][A-Za-z0-9._:-]*'
# Longest Agent failure reason.
FAILURE_REASON_MAX_LENGTH = 512
# Most digits in a request number.
REQUEST_ID_MAX_DIGITS = 20

# A decoded Runtime message: a command envelope {'id', 'command', 'data'} or a reply
# whose 'kind' is 'ready', 'notice', 'snapshot', 'ended' or 'reply'.
Message = Dict[str, Any]


# Token counts a model receipt may carry, each a non-negative integer.
USAGE_FIELDS = frozenset(('input_tokens', 'output_tokens', 'total_tokens', 'cached_tokens'))


# The model version the provider says served the reply, recorded for reproducibility.
SERVED_MODEL = 'model'
MAX_MODEL_NAME = 128
# A role name an Agent attached to the request (TradingAgent ``label``).
USAGE_LABEL = 'label'
MAX_LABEL = 64


def _served_model(value: Any) -> bool:
    return type(value) is str and 0 < len(value) <= MAX_MODEL_NAME and value.isprintable()


def valid_usage(value: Any) -> bool:
    return (
        type(value) is dict
        and set(value) <= USAGE_FIELDS | {SERVED_MODEL, USAGE_LABEL}
        and all(
            (
                _served_model(v)
                if k == SERVED_MODEL
                else (
                    type(v) is str and 0 < len(v) <= MAX_LABEL and v.isprintable()
                    if k == USAGE_LABEL
                    else type(v) is int and 0 <= v < 2**53
                )
            )
            for k, v in value.items()
        )
    )


def model_usage(reply: Any) -> Dict[str, Any]:
    """Token counts of a provider reply's 'usage', and the model version that served it.

    Only known integer fields are kept, plus the reply's ``model`` text when present.
    """
    usage = reply.get('usage') if isinstance(reply, dict) else None
    if not isinstance(usage, dict):
        return {}
    counts = {
        key: usage[key]
        for key in ('input_tokens', 'output_tokens', 'total_tokens')
        if type(usage.get(key)) is int and 0 <= usage[key] < 2**53
    }
    cached = (usage.get('input_tokens_details') or {}).get('cached_tokens')
    if type(cached) is int and 0 <= cached < 2**53:
        counts['cached_tokens'] = cached
    served = reply.get(SERVED_MODEL)
    if counts and _served_model(served):
        counts[SERVED_MODEL] = served
    return counts


def validate_payload(name: Any, data: Any) -> Dict[str, Any]:
    """Check a command's name and exact fields; return ``data`` unchanged."""
    if type(name) is not str or name not in COMMANDS or type(data) is not dict:
        raise ContractError('invalid runtime command')
    required = FIELDS[name]
    optional = (
        {'result', 'error', 'retry_after_seconds'}
        if name == 'receive_attempt'
        else {'error', 'retry_after_seconds', 'usage'} if name == 'model_receipt' else set()
    )
    if required - set(data) or set(data) - (required | optional):
        raise ContractError('invalid runtime command fields')
    for field in ('work_id', 'request_id', 'epoch', 'attempt_id'):
        if field in data and (
            type(data[field]) is not str
            or len(data[field]) > IDENTITY_MAX_LENGTH
            or re.fullmatch(IDENTITY_PATTERN, data[field]) is None
        ):
            raise ContractError('invalid runtime capability identity')
    if name == 'model_receipt' and (
        type(data['received_ns']) is not int or data['received_ns'] < 0
    ):
        raise ContractError('invalid trusted model receipt')
    if name == 'model_receipt' and 'usage' in data and not valid_usage(data['usage']):
        raise ContractError('invalid model token usage')
    if name == 'agent_failed' and (
        type(data['reason']) is not str or len(data['reason']) > FAILURE_REASON_MAX_LENGTH
    ):
        raise ContractError('invalid runtime failure reason')
    return data


def packet(value: Any) -> bytes:
    """Encode a message for a pipe, refusing one larger than MAX_BYTES."""
    raw = encode(value).encode('utf-8')
    if len(raw) > MAX_BYTES:
        raise ContractError('runtime message exceeds byte limit')
    return raw


def reject_constant(value: str) -> NoReturn:
    raise ValueError('nonfinite runtime JSON')


def unique_pairs(pairs: List[Tuple[str, Any]]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate runtime JSON key')
        result[key] = value
    return result


def unpack(raw: bytes) -> Message:
    """Decode one pipe message, rejecting NaN/Infinity, duplicate keys and non-objects."""
    if len(raw) > MAX_BYTES:
        raise ContractError('runtime message exceeds byte limit')
    try:
        value = json.loads(raw, parse_constant=reject_constant, object_pairs_hook=unique_pairs)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ContractError('invalid runtime JSON') from exc
    if type(value) is not dict:
        raise ContractError('runtime message must be an object')
    return copy_json(value)


def command(value: Message) -> Message:
    """Check a command envelope's fields, request number and payload."""
    if set(value) != {'id', 'command', 'data'}:
        raise ContractError('invalid runtime envelope')
    if (
        type(value['id']) is not str
        or not value['id'].isascii()
        or not value['id'].isdigit()
        or len(value['id']) > REQUEST_ID_MAX_DIGITS
        or value['id'].startswith('0')
    ):
        raise ContractError('invalid runtime request identity')
    validate_payload(value['command'], value['data'])
    return value


def reply(value: Message) -> Message:
    """Check a Runtime reply's kind and exact fields."""
    fields = {
        'ready': {'kind'},
        'notice': {'kind', 'event', 'details'},
        'snapshot': {'kind', 'snapshot'},
        'ended': {'kind', 'snapshot'},
        'reply': {'kind', 'id', 'result', 'error', 'snapshot'},
    }
    kind = value.get('kind')
    if type(kind) is not str or kind not in fields or set(value) != fields[kind]:
        raise ContractError('invalid runtime reply fields')
    if 'snapshot' in value and type(value['snapshot']) is not dict:
        raise ContractError('invalid runtime snapshot')
    if kind == 'notice' and (type(value['event']) is not str or type(value['details']) is not dict):
        raise ContractError('invalid runtime notice')
    if kind == 'reply' and value['error'] is not None:
        error = value['error']
        if (
            type(error) is not dict
            or set(error) != {'code', 'message'}
            or type(error['code']) is not str
            or type(error['message']) is not str
        ):
            raise ContractError('invalid runtime error')
    return value
