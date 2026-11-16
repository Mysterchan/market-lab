"""Preparation settings for built-in policy experiments; agent settings use their own configuration validator."""

from __future__ import annotations

from typing import Any, Dict, Optional

from sim_platform.domain.json_values import copy_json
from sim_platform.domain.models import ContractError, integer
from sim_platform.decisions.policy.contracts import make_policy
from sim_platform.decisions.policy.models import DEFAULT_HOLD_NS, DEFAULT_RULE_QUANTITY

# Symbol prepared when a caller does not choose one.
DEFAULT_SYMBOL = 'AAPL'

# Validated Human/Rule/Model experiment settings: 'symbol', 'start_mode', 'start_time_ns',
# 'start_date' (custom start on a date of a price-bar recording, or None), 'quantity',
# 'hold_ns', 'news' (path or None), 'allow_implicit_eof', 'allow_short' (short selling
# under docs/reference/orders.md), 'symbols' (two or more, for an experiment on several
# symbols; see docs/architecture/multi-symbol-design.md), 'book' (a price-bar recording's
# {'spread', 'depth'}, or None) and, for the model method, 'model' (see MODEL_DEFAULTS).
PolicySettings = Dict[str, Any]
# Model method settings: the user's file, values passed to it, completed bars per call,
# the interpreter that runs it (None: the App's Python) and the seconds one call may take.
MODEL_DEFAULTS: Dict[str, Any] = dict(
    model_file=None,
    model_parameters={},
    lookback_bars=50,
    model_python=None,
    model_timeout_seconds=10,
)


def model_settings(values: Dict[str, Any]) -> Dict[str, Any]:
    """Check the model method's settings; the file itself is checked when the run starts."""
    if not isinstance(values['model_file'], str) or not values['model_file']:
        raise ContractError('the model method needs model_file, the path of a .py file')
    if not isinstance(values['model_parameters'], dict):
        raise ContractError('model_parameters must be a JSON object')
    integer(values['lookback_bars'], 'lookback_bars', 1)
    if values['model_python'] is not None and not isinstance(values['model_python'], str):
        raise ContractError('model_python must be an interpreter path')
    timeout = values['model_timeout_seconds']
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 600:
        raise ContractError('model_timeout_seconds must be between 0 and 600')
    return {key: values[key] for key in MODEL_DEFAULTS}


def policy_settings(method: str, settings: Optional[Dict[str, Any]]) -> PolicySettings:
    """Fill defaults for a policy experiment and reject unknown or invalid settings."""
    defaults = dict(
        symbol=DEFAULT_SYMBOL,
        start_mode='first_book',
        start_time_ns=None,
        quantity=DEFAULT_RULE_QUANTITY,
        hold_ns=DEFAULT_HOLD_NS,
        news=None,
        allow_implicit_eof=False,
        book=None,
        start_date=None,
        allow_short=False,
        symbols=None,
        **MODEL_DEFAULTS,
    )
    if settings is None:
        settings = {}
    if not isinstance(settings, dict) or set(settings) - set(defaults):
        raise ContractError('unknown policy experiment settings')
    values = dict(defaults, **copy_json(settings))
    integer(values['quantity'], 'quantity', 1)
    integer(values['hold_ns'], 'hold duration')
    model_given = any(key in settings for key in MODEL_DEFAULTS)
    if method == 'model':
        values['model'] = model_settings(values)
    elif model_given:
        raise ContractError('model settings need method=model')
    else:
        make_policy(method, values['quantity'], values['hold_ns'])
    for key in MODEL_DEFAULTS:
        values.pop(key)
    if type(values['allow_implicit_eof']) is not bool:
        raise ContractError('allow_implicit_eof must be boolean')
    if type(values['allow_short']) is not bool:
        raise ContractError('allow_short must be boolean')
    if values['symbols'] is not None:
        if not isinstance(values['symbols'], list) or any(
            not isinstance(s, str) for s in values['symbols']
        ):
            raise ContractError('symbols must be a list of symbols')
        if method not in ('human', 'model', 'rule', 'hold'):
            raise ContractError('several symbols work with the manual, rule and model methods')
    from sim_platform.market.prepared.bars import book_options

    values['book'] = book_options(values['book'])
    return values
