"""Trusted Agent read-tool example setup; no model, uploaded code or credentials.

Returned controller is Platform-owned. play() starts the background owner;
request_tool() returns a Future whose message['result'] is the wire response.
References below explicitly describe a local harness, not validated providers.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Optional

from sim_platform.agents.tools.protocol.contracts import READ_TOOLS
from .market_demo import write_fixture

if TYPE_CHECKING:  # Annotation only.
    from sim_platform.application.platform import Platform
    from sim_platform.research.controller import ResearchController

# Default experiment window: two seconds after the open, for four seconds.
DEFAULT_START_NS = 34_202_000_000_000
DEFAULT_END_OFFSET_NS = 4_000_000_000
# Starting cash for the demo account (money units).
ACCOUNT_CASH = 100_000_000


def prepare_demo(
    app: Platform,
    directory: Path,
    *,
    source: Optional[Path] = None,
    start_ns: int = DEFAULT_START_NS,
    end_offset_ns: int = DEFAULT_END_OFFSET_NS,
) -> ResearchController:
    """Save a read-only fixture Agent and open a live experiment for it."""
    profile = app.create_profile('Agent read example')
    account = app.create_account(profile, 'Cash', ACCOUNT_CASH)
    if source is None:
        source, _ = write_fixture(directory)
    definition: Dict[str, Any] = dict(
        agent_id='fixture-reader',
        revision='1',
        name='Read-only fixture harness',
        integration='configured',
        carrier='platform',
        model_profile_ref='fixture-no-provider',
        initial_prompt='Use read tools.',
        parameters={},
        requires=['market.quote'],
        schedule=dict(mode='continuous', busy_event_policy='preserve_interval', events=[]),
    )
    settings: Dict[str, Any] = dict(
        model_profile_revision='1',
        model_parameters={},
        granted_tools=sorted(READ_TOOLS),
        symbol='AAPL',
        start_ns=str(start_ns),
        end_ns=str(start_ns + end_offset_ns),
        speed='1x',
        source_manifest_ref='local-read-fixture',
        timeout_seconds='30',
        retry=dict(
            max_attempts=3,
            format_max_attempts=3,
            base_delay_seconds='1',
            max_delay_seconds='8',
            retry_classes=['transport', 'rate_limit', 'temporary_provider', 'timeout'],
        ),
        limits=dict(
            tool_request_bytes=1048576,
            tool_response_bytes=1048576,
            pending_requests=64,
            page_items=100,
            event_buckets=16,
            depth_levels=5,
            json_depth=32,
        ),
        lag=dict(threshold_seconds='2', window_seconds='5', profile_ref='fixture-only'),
        support_profile_ref='local-fixture-only',
    )
    app.create_agent(profile, definition)
    return app.open_experiment(
        profile,
        account,
        method='agent',
        agent_id='fixture-reader',
        revision=1,
        source=source,
        settings=settings,
        log_path=directory / 'agent.jsonl',
        execution_mode='live',
    )
