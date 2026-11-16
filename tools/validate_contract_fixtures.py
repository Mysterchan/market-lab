"""Check Agent contract artifacts without importing or executing the platform.

Supports exactly the JSON Schema keywords used by the local Agent schema; rejects
unknown assertions. This is not a general Draft 2020-12 implementation, runtime
acceptance test, sandbox proof or provider integration test. Uses only stdlib.
"""

import json
import math
import re
from decimal import Decimal
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / 'tests' / 'fixtures' / 'agent-contracts'
KEYWORDS = {
    '$schema',
    '$id',
    '$defs',
    '$ref',
    'title',
    'description',
    'type',
    'enum',
    'const',
    'properties',
    'required',
    'additionalProperties',
    'items',
    'minItems',
    'maxItems',
    'uniqueItems',
    'minimum',
    'maximum',
    'minLength',
    'maxLength',
    'pattern',
    'oneOf',
    'anyOf',
}


def object_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key: ' + key)
        result[key] = value
    return result


def load(path):
    def nonfinite(value):
        raise ValueError('nonfinite JSON: ' + value)

    return json.loads(path.read_text(), object_pairs_hook=object_pairs, parse_constant=nonfinite)


def inspect_schema(node, root):
    unknown = set(node) - KEYWORDS
    if unknown:
        raise ValueError('unsupported schema keyword: ' + repr(unknown))
    if '$ref' in node:
        name = node['$ref'][8:]
        assert node['$ref'] == '#/$defs/' + name and name in root['$defs']
    for key in ('$defs', 'properties'):
        for child in node.get(key, {}).values():
            inspect_schema(child, root)
    for key in ('oneOf', 'anyOf'):
        for child in node.get(key, []):
            inspect_schema(child, root)
    for key in ('items', 'additionalProperties'):
        if isinstance(node.get(key), dict):
            inspect_schema(node[key], root)


def valid(value, node, root):
    if '$ref' in node:
        return valid(value, root['$defs'][node['$ref'][8:]], root)
    if 'oneOf' in node and sum(valid(value, sub, root) for sub in node['oneOf']) != 1:
        return False
    if 'anyOf' in node and not any(valid(value, sub, root) for sub in node['anyOf']):
        return False
    if 'const' in node and (type(value) is not type(node['const']) or value != node['const']):
        return False
    if 'enum' in node and not any(
        type(value) is type(item) and value == item for item in node['enum']
    ):
        return False
    expected = node.get('type')
    types = {
        'null': value is None,
        'boolean': type(value) is bool,
        'integer': type(value) is int,
        'number': type(value) in (int, float) and math.isfinite(value),
        'string': isinstance(value, str),
        'array': isinstance(value, list),
        'object': isinstance(value, dict),
    }
    if expected and not types[expected]:
        return False
    if isinstance(value, str):
        if not node.get('minLength', 0) <= len(value) <= node.get('maxLength', math.inf):
            return False
        if 'pattern' in node and re.search(node['pattern'], value) is None:
            return False
    if type(value) in (int, float):
        if not node.get('minimum', -math.inf) <= value <= node.get('maximum', math.inf):
            return False
    if isinstance(value, list):
        if not node.get('minItems', 0) <= len(value) <= node.get('maxItems', math.inf):
            return False
        if node.get('uniqueItems') and len({json.dumps(v, sort_keys=True) for v in value}) != len(
            value
        ):
            return False
        if 'items' in node and not all(valid(v, node['items'], root) for v in value):
            return False
    if isinstance(value, dict):
        if any(key not in value for key in node.get('required', [])):
            return False
        props = node.get('properties', {})
        for key, item in value.items():
            if key in props:
                if not valid(item, props[key], root):
                    return False
            elif node.get('additionalProperties') is False:
                return False
            elif isinstance(node.get('additionalProperties'), dict):
                if not valid(item, node['additionalProperties'], root):
                    return False
    return True


def check_formula(check):
    """Independent arithmetic/cutoff oracle, not the platform implementation."""
    kind, args, expected = check['kind'], check['input'], check['expected']
    if kind == 'interval':
        actual = args['finished'] + args['interval']
    elif kind == 'timeout_remaining':
        actual = args['timeout'] - args['active_elapsed']
    elif kind == 'partial_cleanup':
        actual = {
            'spent': args['filled'] * (args['price'] + args['fee']),
            'released': (args['quantity'] - args['filled']) * (args['limit'] + args['fee']),
            'held': args['filled'],
        }
    elif kind == 'available_cash':
        actual = args['cash'] - sum(q * (p + args['fee']) for q, p in args['open_orders'])
    elif kind == 'available_shares':
        actual = args['held'] - sum(args['reserved'])
    elif kind == 'shared_supply':
        remaining, actual = args['supply'], []
        for quantity in args['requested']:
            fill = min(remaining, quantity)
            actual.append(fill)
            remaining -= fill
    elif kind == 'effective_time':
        actual = max(args['receipt_target'], args['executed_target'])
    elif kind == 'source_prefix':
        stamps = args['timestamps']
        if any(a > b for a, b in zip(stamps, stamps[1:])):
            actual = 'SOURCE_ERROR'
        else:
            actual = [
                i + 1 for i, t in enumerate(stamps) if t <= args['cutoff'] and t < args['end']
            ]
    elif kind == 'bar_cutoff':
        observed = min(args['cutoff'], args['period_end'])
        actual = {'observed_through': observed, 'complete': observed == args['period_end']}
    elif kind == 'boundary_order':
        order = {
            'fault': 0,
            'end': 1,
            'stop': 2,
            'pause': 3,
            'market': 4,
            'model_timeout': 5,
            'model_reply': 6,
            'tool': 7,
            'wake': 8,
        }
        actual = sorted(args['events'], key=lambda event: (order[event['kind']], event['receipt']))
    elif kind == 'attempt_valid':
        actual = (
            args['reply_attempt'] == args['current_attempt']
            and args['reply_active'] < args['deadline']
            and not args['stopped']
        )
    elif kind == 'retry_delay':
        actual = min(args['cap'], args['base'] * 2 ** (args['failed_attempt'] - 1))
    elif kind == 'dedup':
        actual = (
            'original_result'
            if args['old_payload'] == args['new_payload']
            else 'REQUEST_ID_CONFLICT'
        )
    else:
        raise ValueError('unknown arithmetic check: ' + kind)
    assert actual == expected, (kind, actual, expected)


def semantic_error(value):
    """Check standalone invariants; owner-dependent checks remain runtime gates."""
    if 'integration' in value:
        if int(value['revision']) < 1:
            return 'INVALID_ARGUMENT'
        schedule = value.get('schedule', {})
        if schedule.get('mode') == 'interval' and Decimal(schedule['interval_seconds']) <= 0:
            return 'INVALID_ARGUMENT'
    elif 'definition' in value:
        if not 0 <= int(value['start_ns']) < int(value['end_ns']) < 86_400_000_000_000:
            return 'INVALID_ARGUMENT'
        if Decimal(value['timeout_seconds']) <= 0:
            return 'INVALID_ARGUMENT'
        retry = value['retry']
        if Decimal(retry['base_delay_seconds']) > Decimal(retry['max_delay_seconds']):
            return 'INVALID_ARGUMENT'
        if (
            Decimal(value['lag']['threshold_seconds']) <= 0
            or Decimal(value['lag']['window_seconds']) <= 0
        ):
            return 'INVALID_ARGUMENT'
        if not set(value['definition']['requires']) <= set(value['granted_tools']):
            return 'TOOL_UNAVAILABLE'
        return semantic_error(value['definition'])
    elif 'arguments' in value:
        if int(value['epoch']) < 1:
            return 'INVALID_ARGUMENT'
        tool, args = value['tool'], value['arguments']
        if 'symbol' in args and args['symbol'] != args['symbol'].strip():
            return 'INVALID_ARGUMENT'
        if tool == 'orders.submit_limit':
            price = Decimal(args['limit_price_usd']) * 10000
            if price <= 0 or price * args['quantity_shares'] > 2**63 - 1:
                return 'INVALID_ARGUMENT'
        if (
            tool == 'market.bars'
            and not 0 <= int(args['start_ns']) < int(args['end_ns']) < 86_400_000_000_000
        ):
            return 'INVALID_ARGUMENT'
        if tool == 'control.wait':
            if any(c['kind'] == 'after' and Decimal(c['seconds']) <= 0 for c in args['conditions']):
                return 'INVALID_ARGUMENT'
    elif value.get('status') == 'ok':
        data = value['data']
        if value['tool'] == 'orders.submit_limit':
            if (
                data['outcome'] == 'accepted'
                and (data['order'] is None or data['reason_code'] is not None)
                or data['outcome'] == 'rejected'
                and data['reason_code'] is None
            ):
                return 'INTERNAL_STATE_ERROR'
        orders = []
        if value['tool'] == 'orders.get':
            orders = [data]
        elif value['tool'] in ('orders.submit_limit', 'orders.cancel') and data.get('order'):
            orders = [data['order']]
        elif value['tool'] == 'orders.list':
            orders = data['items']
        for order in orders:
            if (
                order['filled_shares'] > order['quantity_shares']
                or order['remaining_shares'] != order['quantity_shares'] - order['filled_shares']
            ):
                return 'INTERNAL_STATE_ERROR'
            if order['status'] == 'filled' and order['remaining_shares'] != 0:
                return 'INTERNAL_STATE_ERROR'
    return None


def main():
    schema = load(ARTIFACTS / 'contracts.schema.json')
    inspect_schema(schema, schema)
    examples = load(ARTIFACTS / 'wire-examples.json')['examples']
    assert len({case['id'] for case in examples}) == len(examples)
    for case in examples:
        assert valid(case['value'], schema, schema) == case['valid'], case['id']
        if case['valid']:
            assert semantic_error(case['value']) == case.get('semantic_error'), case['id']
    # Negative controls demonstrate rejection rather than only successful parsing.
    for raw in ('{"x":1,"x":2}', '{"x":NaN}', '{"x":Infinity}'):
        try:
            json.loads(
                raw,
                object_pairs_hook=object_pairs,
                parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
            )
        except ValueError:
            continue
        raise AssertionError('invalid JSON was accepted')
    cases = load(ARTIFACTS / 'semantic-cases.json')['cases']
    ids = [case['id'] for case in cases]
    assert ids == ['C%02d' % i for i in range(1, 29)], ids
    count = 0
    for case in cases:
        assert case['events'] and case['expected'] and case['requirements']
        for event in case['events']:
            assert set(event) == {'at_active_ns', 'kind', 'payload'}
            assert re.fullmatch(r'0|[1-9][0-9]*', event['at_active_ns'])
        for check in case['checks']:
            check_formula(check)
            count += 1
    print(
        'PASS: %d wire examples; %d semantic case records; %d independent arithmetic/order checks; strict JSON negative controls'
        % (len(examples), len(cases), count)
    )
    print(
        'Not executed: runtime traces, provider, SQL/native faults, OS isolation, real-time pacing or Replay integration.'
    )


if __name__ == '__main__':
    main()
