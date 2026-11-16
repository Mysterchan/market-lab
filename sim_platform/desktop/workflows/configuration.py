"""User-facing defaults translated into the existing Agent configuration contract."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

from sim_platform.domain.models import ContractError

# Desktop Agent defaults: per-request limits and retry policy.
TOOL_MESSAGE_BYTES = 1048576
PENDING_REQUESTS = 128
PAGE_ITEMS = 100
EVENT_BUCKETS = 16
DEPTH_LEVELS = 5
JSON_DEPTH = 32
MAX_ATTEMPTS = 3


def agent_settings(
    definition: Dict[str, Any],
    symbol: str,
    start_ns: int,
    end_ns: int,
    timeout_seconds: Union[int, str],
    pacing: str = 'live',
) -> Dict[str, Any]:
    """Experiment settings for a saved Agent with the desktop's fixed limits and retries.

    ``pacing`` is 'live' (speed '1x') or 'turn' (turn-based: the market waits for each
    decision).
    """
    if pacing not in ('live', 'turn'):
        raise ContractError("Agent pacing is 'live' or 'turn'")
    return dict(
        model_profile_revision='1',
        model_parameters={},
        granted_tools=definition['requires'],
        symbol=symbol,
        start_ns=str(start_ns),
        end_ns=str(end_ns),
        speed='turn' if pacing == 'turn' else '1x',
        source_manifest_ref='desktop-market',
        timeout_seconds=str(timeout_seconds),
        retry=dict(
            max_attempts=MAX_ATTEMPTS,
            format_max_attempts=MAX_ATTEMPTS,
            base_delay_seconds='1',
            max_delay_seconds='8',
            retry_classes=['transport', 'rate_limit', 'temporary_provider', 'timeout'],
        ),
        limits=dict(
            tool_request_bytes=TOOL_MESSAGE_BYTES,
            tool_response_bytes=TOOL_MESSAGE_BYTES,
            pending_requests=PENDING_REQUESTS,
            page_items=PAGE_ITEMS,
            event_buckets=EVENT_BUCKETS,
            depth_levels=DEPTH_LEVELS,
            json_depth=JSON_DEPTH,
        ),
        lag=dict(threshold_seconds='2', window_seconds='5', profile_ref='desktop-local'),
        support_profile_ref='desktop-local',
    )


def schedule(
    mode: str,
    interval: Any,
    busy_policy: str = 'preserve_interval',
    events: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """An Agent schedule; event mode needs at least one event."""
    value = dict(mode=mode, busy_event_policy=busy_policy, events=events or [])
    if mode == 'interval':
        value['interval_seconds'] = str(interval)
    if mode == 'event' and not value['events']:
        raise ContractError('Choose at least one event for an event-triggered Agent')
    return value
