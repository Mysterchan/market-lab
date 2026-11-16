"""Pure validation for agent definitions and experiment configuration.

Read validate_definition and validate_run_configuration first: each lists its
validation stages in contract order. Internal helpers validate one section.
validate_experiment_settings handles the caller-owned part of a run config;
validate_identifier and validate_revision serve application lookup arguments.

Successful configuration validation returns detached JSON. Failures raise
AgentConfigurationError with a stable code, without echoing supplied values.
This module does not resolve references, read schemas, or execute agents.
Reference schemas and examples document the same finite-JSON boundaries.
"""

from __future__ import annotations

from decimal import Decimal
import re
from typing import Any, Iterable

from sim_platform.domain.json_values import copy_json, encode
from sim_platform.domain.models import ContractError

MAX_INTEGER = 2**63 - 1
NANOSECONDS_PER_SECOND = 1_000_000_000
NANOSECONDS_PER_DAY = 86_400 * NANOSECONDS_PER_SECOND
# Market time counts from the first day's midnight and may span many days; 100 years
# is the bound (same value as market.models.MAX_TIME_NS).
MAX_MARKET_TIME_NS = 36_525 * NANOSECONDS_PER_DAY
MAX_CONFIGURATION_BYTES = 1_048_576
# Upper bound of a granted or required tool list; room for every platform tool.
MAX_CAPABILITIES = 32
MAX_JSON_DEPTH = 32

AVAILABLE_TOOLS = frozenset(
    (
        'environment.describe',
        'market.quote',
        'market.depth',
        'market.trades',
        'market.bars',
        'news.list',
        'fundamentals.get',
        'documents.search',
        'documents.read',
        'journal.write',
        'account.snapshot',
        'orders.list',
        'orders.get',
        'state.snapshot',
        'orders.submit_limit',
        'orders.submit_market',
        'orders.cancel',
        'requests.get',
        'control.finish',
        'control.wait',
        'model.request',
    )
)
RESOURCE_LIMIT_RANGES = {
    'tool_request_bytes': (1024, 1_048_576),
    'tool_response_bytes': (1024, 1_048_576),
    'pending_requests': (1, 1024),
    'page_items': (1, 100),
    'event_buckets': (1, 16),
    'depth_levels': (1, 5),
    'json_depth': (1, 32),
}
DEFINITION_FIELDS = frozenset(
    (
        'agent_id',
        'revision',
        'name',
        'integration',
        'carrier',
        'model_profile_ref',
        'initial_prompt',
        'parameters',
        'requires',
    )
)
RUN_FIELDS = frozenset(
    (
        'run_id',
        'definition',
        'model_profile_revision',
        'model_parameters',
        'granted_tools',
        'symbol',
        'start_ns',
        'end_ns',
        'speed',
        'source_manifest_ref',
        'initial_account_ref',
        'execution_config_ref',
        'timeout_seconds',
        'retry',
        'limits',
        'lag',
        'support_profile_ref',
    )
)
PLATFORM_RUN_FIELDS = frozenset(
    (
        'run_id',
        'definition',
        'initial_account_ref',
        'execution_config_ref',
    )
)
CALLER_RUN_FIELDS = RUN_FIELDS - PLATFORM_RUN_FIELDS
# Optional data attached to a run: 'fundamentals_path', a fundamentals CSV, an SEC
# EDGAR companyfacts JSON, an ALFRED vintage CSV or a folder of them (market.fundamentals)
# read through fundamentals.get; 'documents_path', a documents JSON (market.documents)
# read through documents.search and documents.read.
# 'budget' caps the run's model use: 'max_tokens' (input plus output tokens) and/or
# 'max_usd' priced by 'prices' ('input' and 'output' USD per million tokens, optionally
# 'cached_input'). The run ends cleanly, recorded as ended by its budget, before a
# model request once the budget is spent.
# 'sandbox' runs an imported Agent's process in an OS sandbox: {'kind': 'auto', 'podman',
# 'bubblewrap' or 'macos', 'network': 'none', 'local' or 'model'}
# (agents.hosting.processes.sandbox).
OPTIONAL_RUN_FIELDS = frozenset(
    ('fundamentals_path', 'documents_path', 'symbols', 'budget', 'sandbox')
)
# Price fields a budget may give, in USD per million tokens.
BUDGET_PRICES = frozenset(('input', 'output', 'cached_input'))
# Most symbols one Agent experiment may trade.
MAX_SYMBOLS = 20
MAX_PATH_LENGTH = 4096
CREDENTIAL_FIELD_NAMES = frozenset(
    (
        'apikey',
        'accesskey',
        'accesstoken',
        'refreshtoken',
        'secretkey',
        'clientsecret',
        'password',
        'authorization',
    )
)
RETRY_CLASSES = frozenset(
    (
        'transport',
        'rate_limit',
        'temporary_provider',
        'timeout',
    )
)


class AgentConfigurationError(ContractError):
    """A contract error whose code can be handled without parsing its message."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f'{code}: {message}')


def _reject(message: str, code: str = 'INVALID_ARGUMENT') -> None:
    raise AgentConfigurationError(code, message)


def _validate_json_members(item: Any, depth: int) -> None:
    if depth > MAX_JSON_DEPTH:
        _reject('configuration exceeds JSON depth limit')
    if type(item) is dict:
        for key, child in item.items():
            if not isinstance(key, str):
                _reject('JSON object keys must be strings', 'INVALID_SCHEMA')
            normalized_key = re.sub(r'[_-]', '', key).lower()
            if normalized_key in CREDENTIAL_FIELD_NAMES:
                _reject('credentials must be stored separately, using references', 'FORBIDDEN')
            _validate_json_members(child, depth + 1)
    elif type(item) is list:
        for child in item:
            _validate_json_members(child, depth + 1)
    elif item is not None and type(item) not in (str, bool, int, float):
        _reject('configuration must be finite JSON', 'INVALID_SCHEMA')


def _copy_configuration(value: Any) -> dict:
    """Bound JSON size/depth and reject known credential keys before copying.

    encode rejects NaN/infinity. Credential keys are normalized for underscores,
    hyphens, and case; arbitrary strings are not inspected for hidden secrets.
    """
    _validate_json_members(value, depth=0)
    try:
        encoded = encode(value)
    except ContractError:
        _reject('configuration must be finite JSON', 'INVALID_SCHEMA')
    if len(encoded.encode('utf-8')) > MAX_CONFIGURATION_BYTES:
        _reject('configuration exceeds byte limit')
    return copy_json(value)


def _validate_fields(value: dict, required: Iterable[str], optional: Iterable[str] = ()) -> None:
    if type(value) is not dict:
        _reject('configuration fields do not match the contract', 'INVALID_SCHEMA')
    actual_fields = set(value)
    required_fields = set(required)
    allowed_fields = required_fields | set(optional)
    if required_fields - actual_fields or actual_fields - allowed_fields:
        _reject('configuration fields do not match the contract', 'INVALID_SCHEMA')


def validate_identifier(value: str) -> str:
    """Check an identity/reference string; return it without modification."""
    if not isinstance(value, str) or len(value) > 128:
        _reject('invalid identifier')
    if re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]*', value) is None:
        _reject('invalid identifier')
    return value


def _validate_integer(value: int, minimum: int = 0, maximum: int = MAX_INTEGER) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        _reject('integer is outside the supported range')
    return value


def _validate_counter(value: str, minimum: int = 0, maximum: int = MAX_INTEGER) -> int:
    """Parse a canonical decimal counter; JSON wire values use strings."""
    if not isinstance(value, str) or len(value) > 20:
        _reject('counter must be a decimal string')
    if re.fullmatch(r'0|[1-9][0-9]*', value) is None:
        _reject('counter must be a decimal string')
    return _validate_integer(int(value), minimum=minimum, maximum=maximum)


def _validate_text(value: str, maximum: int = 256) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        _reject('invalid text length')


def _validate_capabilities(value: list) -> None:
    if type(value) is not list or len(value) > MAX_CAPABILITIES:
        _reject('capabilities must be a bounded list', 'INVALID_SCHEMA')
    if any(type(item) is not str for item in value):
        _reject('capabilities must be a bounded list', 'INVALID_SCHEMA')
    if len(set(value)) != len(value):
        _reject('duplicate capability')
    if set(value) - AVAILABLE_TOOLS:
        _reject('required tool is unavailable', 'TOOL_UNAVAILABLE')


def _validate_definition_metadata(definition: dict) -> None:
    validate_identifier(definition['agent_id'])
    _validate_counter(definition['revision'], minimum=1)
    _validate_text(definition['name'])
    _validate_text(definition['initial_prompt'], maximum=65_536)
    validate_identifier(definition['model_profile_ref'])
    if type(definition['parameters']) is not dict:
        _reject('parameters must be an object', 'INVALID_SCHEMA')
    _validate_capabilities(definition['requires'])
    if definition['integration'] not in ('configured', 'python_strategy', 'python_carrier'):
        _reject('unsupported integration')


def _validate_seconds(value: str, *, positive: bool = False) -> int:
    """Convert exact decimal seconds to int64 nanoseconds, without float rounding."""
    if not isinstance(value, str) or len(value) > 24:
        _reject('seconds must be an exact decimal string')
    if re.fullmatch(r'(0|[1-9][0-9]*)(\.[0-9]{1,9})?', value) is None:
        _reject('seconds must be an exact decimal string')
    nanoseconds = Decimal(value) * NANOSECONDS_PER_SECOND
    minimum = 1 if positive else 0
    if not minimum <= nanoseconds <= MAX_INTEGER:
        _reject('duration is outside the supported range')
    return int(nanoseconds)


def _validate_symbol(value: str) -> None:
    if not isinstance(value, str) or not 1 <= len(value) <= 8 or value != value.strip():
        _reject('symbol must be supported uppercase ASCII text')
    if re.fullmatch(r'[A-Z0-9. -]+', value) is None:
        _reject('symbol must be supported uppercase ASCII text')


def _validate_event_filter(event: dict) -> None:
    _validate_fields(event, ('kind',), ('symbol', 'order_id'))
    if event['kind'] == 'market_update':
        if 'order_id' in event:
            _reject('market event cannot filter an order')
        if 'symbol' in event:
            _validate_symbol(event['symbol'])
    elif event['kind'] == 'order_update':
        if 'symbol' in event:
            _reject('order event cannot filter a symbol')
        if 'order_id' in event:
            validate_identifier(event['order_id'])
    else:
        _reject('event kind is unsupported')


def _validate_schedule(schedule: dict) -> None:
    _validate_fields(schedule, ('mode', 'busy_event_policy', 'events'), ('interval_seconds',))
    mode = schedule['mode']
    if mode not in ('continuous', 'interval', 'event'):
        _reject('invalid carrier schedule')
    if schedule['busy_event_policy'] not in ('preserve_interval', 'wake_on_pending_event'):
        _reject('invalid carrier schedule')
    if mode == 'interval':
        if 'interval_seconds' not in schedule:
            _reject('interval schedule requires a duration', 'INVALID_SCHEMA')
        _validate_seconds(schedule['interval_seconds'], positive=True)
    elif 'interval_seconds' in schedule:
        _reject('duration only applies to interval mode', 'INVALID_SCHEMA')
    events = schedule['events']
    if type(events) is not list or len(events) > 8:
        _reject('event schedule requires bounded conditions')
    if mode == 'event' and not events:
        _reject('event schedule requires bounded conditions')
    for event in events:
        _validate_event_filter(event)


def _validate_carrier(definition: dict) -> None:
    custom_carrier = definition['integration'] == 'python_carrier'
    expected_carrier = 'custom' if custom_carrier else 'platform'
    if definition['carrier'] != expected_carrier:
        _reject('carrier differs from integration')
    if custom_carrier:
        if 'schedule' in definition:
            _reject('custom carrier controls its own schedule', 'INVALID_SCHEMA')
        return
    if 'schedule' not in definition:
        _reject('platform carrier requires schedule', 'INVALID_SCHEMA')
    _validate_schedule(definition['schedule'])


def _validate_integration_package(definition: dict) -> None:
    if definition['integration'] == 'configured':
        if 'package' in definition:
            _reject('configured agent cannot contain executable package', 'INVALID_SCHEMA')
        return
    if 'package' not in definition:
        _reject('Python integration requires package identity', 'INVALID_SCHEMA')
    package = definition['package']
    if type(package) is dict and set(package) != {'project_ref'}:
        _reject(
            'import the Python source project; package only accepts project_ref', 'INVALID_SCHEMA'
        )
    _validate_fields(package, ('project_ref',))
    validate_identifier(package['project_ref'])


def validate_definition(value: dict) -> dict:
    """Validate a complete manifest and return a copy independent of the caller.

    Both platform scheduling and custom Python carriers are represented here.
    Package and model references are checked for shape only, not availability.
    """
    definition = _copy_configuration(value)
    _validate_fields(definition, DEFINITION_FIELDS, optional=('schedule', 'package'))
    _validate_definition_metadata(definition)
    _validate_carrier(definition)
    _validate_integration_package(definition)
    return definition


def _validate_run_references(config: dict) -> None:
    for field in (
        'run_id',
        'source_manifest_ref',
        'initial_account_ref',
        'execution_config_ref',
        'support_profile_ref',
    ):
        validate_identifier(config[field])
    _validate_counter(config['model_profile_revision'], minimum=1)
    if type(config['model_parameters']) is not dict:
        _reject('model parameters must be an object', 'INVALID_SCHEMA')


def _validate_market_session(config: dict) -> None:
    _validate_symbol(config['symbol'])
    start_ns = _validate_counter(config['start_ns'], maximum=MAX_MARKET_TIME_NS - 1)
    end_ns = _validate_counter(config['end_ns'], maximum=MAX_MARKET_TIME_NS - 1)
    if start_ns >= end_ns:
        _reject('run requires a nonempty half-open time range')
    # '1x': Live, market time passes with real time. 'turn': turn-based, the market
    # waits while the Agent works (docs/user-guide/evaluation.md#turn-based-agents).
    if config['speed'] not in ('1x', 'turn'):
        _reject("agent experiments run at speed '1x' (Live) or 'turn' (turn-based)")


def _validate_tool_grants(config: dict) -> None:
    _validate_capabilities(config['granted_tools'])
    required = set(config['definition']['requires'])
    granted = set(config['granted_tools'])
    if required - granted:
        _reject('required tool is not granted', 'TOOL_UNAVAILABLE')
    events = config['definition'].get('schedule', {}).get('events', [])
    for event in events:
        if event.get('symbol', config['symbol']) not in run_universe(config):
            _reject('event symbol is outside the experiment')


def _validate_retry(retry: dict) -> None:
    _validate_fields(
        retry,
        (
            'max_attempts',
            'base_delay_seconds',
            'max_delay_seconds',
            'format_max_attempts',
            'retry_classes',
        ),
    )
    _validate_integer(retry['max_attempts'], minimum=1, maximum=100)
    _validate_integer(retry['format_max_attempts'], minimum=1, maximum=100)
    base_delay_ns = _validate_seconds(retry['base_delay_seconds'])
    max_delay_ns = _validate_seconds(retry['max_delay_seconds'])
    if base_delay_ns > max_delay_ns:
        _reject('retry base delay exceeds maximum')
    classes = retry['retry_classes']
    if type(classes) is not list or any(type(item) is not str for item in classes):
        _reject('invalid retry classes')
    if len(set(classes)) != len(classes) or set(classes) - RETRY_CLASSES:
        _reject('invalid retry classes')


def _validate_resource_limits(limits: dict) -> None:
    _validate_fields(limits, RESOURCE_LIMIT_RANGES)
    for field, (minimum, maximum) in RESOURCE_LIMIT_RANGES.items():
        _validate_integer(limits[field], minimum=minimum, maximum=maximum)


def _validate_lag(lag: dict) -> None:
    _validate_fields(lag, ('threshold_seconds', 'window_seconds', 'profile_ref'))
    _validate_seconds(lag['threshold_seconds'], positive=True)
    _validate_seconds(lag['window_seconds'], positive=True)
    validate_identifier(lag['profile_ref'])


def _validate_universe(config: dict) -> None:
    """``symbols``: two or more distinct symbols, the first being ``symbol``."""
    symbols = config.get('symbols')
    if symbols is None:
        return
    if type(symbols) is not list or not 2 <= len(symbols) <= MAX_SYMBOLS:
        _reject(f'symbols must list 2 to {MAX_SYMBOLS} symbols', 'INVALID_SCHEMA')
    for symbol in symbols:
        _validate_symbol(symbol)
    if len(set(symbols)) != len(symbols) or symbols[0] != config['symbol']:
        _reject('symbols must be distinct and start with the experiment symbol')


def run_universe(config: dict) -> list:
    """The symbols an Agent run may trade: ``symbols``, or its one ``symbol``."""
    return list(config.get('symbols') or [config['symbol']])


def _number(value: Any) -> bool:
    """A finite, non-negative JSON number (NaN and infinity are refused by encode)."""
    return type(value) in (int, float) and 0 <= value < 1e15


def _validate_budget(config: dict) -> None:
    budget = config.get('budget')
    if budget is None:
        return
    if type(budget) is not dict or not budget or set(budget) - {'max_tokens', 'max_usd', 'prices'}:
        _reject("budget takes 'max_tokens', 'max_usd' and 'prices'", 'INVALID_SCHEMA')
    if 'max_tokens' in budget:
        _validate_integer(budget['max_tokens'], minimum=1)
    if 'max_usd' in budget:
        if not _number(budget['max_usd']) or budget['max_usd'] <= 0:
            _reject('budget max_usd must be a positive number', 'INVALID_SCHEMA')
        prices = budget.get('prices')
        if (
            type(prices) is not dict
            or not {'input', 'output'} <= set(prices) <= BUDGET_PRICES
            or not all(_number(v) for v in prices.values())
        ):
            _reject(
                "a USD budget needs prices: 'input' and 'output' USD per million tokens",
                'INVALID_SCHEMA',
            )
    if 'max_tokens' not in budget and 'max_usd' not in budget:
        _reject('budget needs max_tokens or max_usd', 'INVALID_SCHEMA')


def _validate_sandbox(config: dict) -> None:
    if config.get('sandbox') is None:
        return
    from sim_platform.agents.hosting.processes.sandbox import validate

    try:
        validate(config['sandbox'])
    except ContractError as exc:
        _reject(str(exc), 'INVALID_SCHEMA')


def _validate_data_sources(config: dict) -> None:
    _validate_universe(config)
    _validate_budget(config)
    _validate_sandbox(config)
    for field in OPTIONAL_RUN_FIELDS - {'symbols', 'budget', 'sandbox'}:
        path = config.get(field)
        if path is not None and (
            not isinstance(path, str) or not path.strip() or len(path) > MAX_PATH_LENGTH
        ):
            _reject(f'{field} must be a file path', 'INVALID_SCHEMA')


def validate_run_configuration(value: dict) -> dict:
    """Validate the complete effective configuration, including platform fields.

    Checks cross-section rules such as required tools being granted and event
    symbols matching the experiment. Does not check account or source state.
    """
    config = _copy_configuration(value)
    _validate_fields(config, RUN_FIELDS, optional=OPTIONAL_RUN_FIELDS)
    _validate_data_sources(config)
    config['definition'] = validate_definition(config['definition'])
    _validate_run_references(config)
    _validate_market_session(config)
    _validate_tool_grants(config)
    _validate_seconds(config['timeout_seconds'], positive=True)
    _validate_retry(config['retry'])
    _validate_resource_limits(config['limits'])
    _validate_lag(config['lag'])
    return config


def validate_experiment_settings(value: object) -> dict:
    """Copy caller settings and reject missing, unknown, or platform-owned fields.

    This is the input boundary, not full run validation: the service supplies
    the four platform fields, then calls validate_run_configuration.
    """
    settings = _copy_configuration(value)
    _validate_fields(settings, CALLER_RUN_FIELDS, optional=OPTIONAL_RUN_FIELDS)
    return settings


def validate_revision(value: int, *, allow_successor: bool = False) -> int:
    """Validate an application revision integer; reject bool and overflow.

    Updates need room for one successor. Manifest revisions remain strings
    and are validated separately inside validate_definition.
    """
    maximum = MAX_INTEGER - 1 if allow_successor else MAX_INTEGER
    return _validate_integer(value, minimum=1, maximum=maximum)
