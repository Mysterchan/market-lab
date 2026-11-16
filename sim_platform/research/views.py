"""Pure presentation of detached research data; no session or persistence access."""

from __future__ import annotations

from typing import Any, Dict

from sim_platform.domain.json_values import copy_json

# Most recent events and decisions shown for a saved run.
VIEW_RECORD_LIMIT = 100

# Detached data captured by ResearchQueries.capture: 'mode' ('agent', 'saved',
# 'simulation' or 'research'); a saved capture carries only 'report', the others carry
# the presentation keys ('market', 'account', 'execution', 'trades', 'bars', 'news',
# 'events', 'decisions', 'handoffs', 'run', 'summary', 'source', ...).
CapturedData = Dict[str, Any]
# The presentation returned to UI and CLI callers, with the presentation keys above.
SessionView = Dict[str, Any]


def session_view(data: CapturedData) -> SessionView:
    """Shape captured data for display; saved reports are reduced to the live view's keys."""
    if data['mode'] == 'agent':
        return copy_json(data)
    if data['mode'] == 'saved':
        report = data['report']
        return dict(
            mode='saved',
            market=report['market'],
            account=report['final'],
            execution=None,
            trades=report['market_data'].get('trades', []),
            bars=report['market_data'].get('bars', []),
            news=report['market_data'].get('news', []),
            daily_closes=report['market_data'].get('daily_closes', []),
            # Present only for a run on several symbols: {symbol: market, bars, closes}.
            markets=report['market_data'].get('markets'),
            events=report['run_events'][-VIEW_RECORD_LIMIT:],
            fills=[e for e in report['run_events'] if e['kind'] == 'fill'],
            decisions=report['decisions'][-VIEW_RECORD_LIMIT:],
            handoffs=report['handoffs'],
            run={k: v for k, v in report['run'].items() if k != 'policy_state'},
            summary=report['summary'],
            source=report['source'],
            # Agent runs only: orders with the reads and notes before them.
            **({'agent_decisions': data['agent_decisions']} if 'agent_decisions' in data else {}),
        )
    value = copy_json(data)
    if value['run'] is not None:
        value['run'] = {k: v for k, v in value['run'].items() if k != 'policy_state'}
    return value
