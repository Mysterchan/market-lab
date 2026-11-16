"""Agent-side completion tools; Runtime remains responsible for the next wake-up."""

from __future__ import annotations

from typing import Any, Dict, List
from .base import PlatformTool, ToolResponse, SYMBOL, counter, text

# A wait names between 1 and this many conditions.
MAX_WAIT_CONDITIONS = 8


def condition(kind: str, **fields: Any) -> Dict[str, Any]:
    """JSON schema for one wait condition of ``kind`` with the given fields."""
    properties = dict(kind=dict(type='string', enum=[kind]), **fields)
    return dict(
        type='object', properties=properties, required=list(properties), additionalProperties=False
    )


class FinishWorkTool(PlatformTool):
    """Proxy for one platform tool, created per Agent client by built_in_tools()."""

    name = 'control.finish'
    category = 'control'
    ends_work = True
    description = (
        'End this work round, even if no trade was made. The configured schedule decides '
        'when the next round starts. Call this alone, after all other tools have returned.'
    )

    def run(self) -> ToolResponse:
        """Finish the current task; this does not stop the experiment or cancel open orders."""
        return self.client.call_tool(self.name, {})


class WaitTool(PlatformTool):
    """Proxy for one platform tool, created per Agent client by built_in_tools()."""

    name = 'control.wait'
    category = 'control'
    ends_work = True
    description = (
        'End this round and wait until any supplied condition holds. Call this alone, '
        'after all other tools have returned. Time waits use experiment time.'
    )
    parameters = dict(
        conditions=dict(
            type='array',
            minItems=1,
            maxItems=MAX_WAIT_CONDITIONS,
            items=dict(
                anyOf=[
                    condition(
                        'after',
                        seconds=text('Positive exact decimal seconds, such as "30" or "0.5".'),
                    ),
                    condition(
                        'market_changed',
                        symbol=SYMBOL,
                        after_sequence=counter('Last observed sequence.'),
                    ),
                    condition(
                        'order_changed',
                        order_id=text('Order ID returned by the platform.'),
                        after_version=counter('Last observed order version.'),
                    ),
                ]
            ),
        )
    )

    def run(self, conditions: List[dict]) -> ToolResponse:
        """Wait for any time/market/order condition; the platform validates current versions."""
        return self.client.call_tool(self.name, dict(conditions=conditions))
