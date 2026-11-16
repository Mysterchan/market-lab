"""Policy invocation and bounded output validation, independent of database lifecycle."""

from __future__ import annotations
import logging

from time import monotonic
import subprocess
from typing import TYPE_CHECKING, Any, Dict

from sim_platform.domain.json_values import copy_json, encode
from sim_platform.decisions.policy.models import (
    MAX_RESPONSE_BYTES,
    DecisionConfig,
    Observation,
    validate_response,
)

_log = logging.getLogger(__name__)

if TYPE_CHECKING:  # Annotation only: keep this module's runtime imports unchanged.
    from sim_platform.decisions.policy.contracts import Policy

# Error text stored in a decision record is cut to this many characters.
ERROR_TEXT_LIMIT = 2048

# The outcome of one policy call: 'status' ('ok', 'timeout' or 'error'), 'error' text,
# the validated 'response', and the raw 'attempted_response' when it was small enough.
InvocationResult = Dict[str, Any]


def invoke(
    policy: Policy,
    observation: Observation,
    state: Any,
    config: DecisionConfig,
    *,
    response: Any = None,
    supplied: bool = False,
) -> InvocationResult:
    """Call the policy (or use a supplied response) and capture any failure as a status.

    Never raises for policy failures: an exception, a time-budget overrun or an invalid
    response becomes a 'timeout' or 'error' result for the caller to audit.
    """
    result: InvocationResult = dict(status='ok', error=None, response=None, attempted_response=None)
    try:
        started = monotonic()
        value = (
            response
            if supplied
            else policy.decide(observation, copy_json(state), config.timeout_ms)
        )
        if (monotonic() - started) * 1000 > config.timeout_ms:
            raise TimeoutError('decision time budget exceeded')
        if len(encode(value).encode()) <= MAX_RESPONSE_BYTES:
            result['attempted_response'] = copy_json(value)
        result['response'] = validate_response(value, observation, config)
    except Exception as exc:
        _log.warning('Handled failure in invoke', exc_info=True)
        result['status'] = (
            'timeout' if isinstance(exc, (TimeoutError, subprocess.TimeoutExpired)) else 'error'
        )
        result['error'] = f'{type(exc).__name__}: {exc}'[:ERROR_TEXT_LIMIT]
    return result
