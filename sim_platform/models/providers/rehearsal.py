"""A deterministic stand-in for a model, to rehearse an Agent without model calls or cost.

Rehearsal checks an Agent's plumbing (roles, tools, data, orders, journal, Replay) before
paying for a real model. It answers each request from the tools offered, like a diligent
but trivial model: it reads one data tool, then ends the role with its finishing tool. A
trader (offered order tools) optionally buys one share at market, writes a journal note
and finishes. Its choices carry no investment meaning; never evaluate its results.

Use it in place of a provider:
``app.configure_experiment_model(experiment, profile, provider=RehearsalProvider('AAPL'))``.
It runs inside the Agent process, so it imports only the standard library.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional, Tuple

# Data tools tried first, with arguments that need only the symbol.
_READS: Tuple[Tuple[str, Callable[[str], Dict[str, Any]]], ...] = (
    ('market_quote', lambda symbol: dict(symbol=symbol)),
    ('news_list', lambda symbol: dict(limit=5)),
    ('fundamentals_get', lambda symbol: dict(symbol=symbol, periods=2)),
    ('documents_search', lambda symbol: dict(query='revenue guidance', limit=2)),
    ('account_snapshot', lambda symbol: {}),
)
# Local tools the reference Agents end a role with, and an argument for each.
_ENDINGS: Tuple[Tuple[str, Dict[str, Any]], ...] = (
    ('report_submit', dict(report='Rehearsal report: no view.')),
    ('memory_store', dict(lesson='Rehearsal lesson.', importance=0.8)),
    ('beliefs_update', dict(beliefs='Rehearsal beliefs.')),
)
MODEL_NAME = 'rehearsal'


class RehearsalProvider:
    """Scripted replies in the Responses format; one instance per experiment."""

    def __init__(self, symbol: str, *, trade: bool = True) -> None:
        self.symbol = symbol
        self.trade = trade
        self.count = 0

    def _call(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        self.count += 1
        return dict(
            type='function_call',
            call_id=f'rehearsal-{self.count}',
            name=name,
            arguments=json.dumps(arguments),
        )

    def choose(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """The next tool call for this request; pure apart from the call counter."""
        offered = {tool['name'] for tool in payload.get('tools') or []}
        used = {
            item.get('name')
            for item in payload.get('input') or []
            if isinstance(item, dict) and item.get('type') == 'function_call'
        }
        trader = 'orders_submit_market' in offered
        if not used:
            for name, build in _READS:
                if name in offered:
                    return self._call(name, build(self.symbol))
        for name, ending in _ENDINGS:
            if name in offered:
                return self._call(name, ending)
        if trader and self.trade and 'orders_submit_market' not in used:
            return self._call(
                'orders_submit_market', dict(symbol=self.symbol, side='buy', quantity_shares=1)
            )
        if 'journal_write' in offered and 'journal_write' not in used:
            return self._call('journal_write', dict(text='Rehearsal decision recorded.'))
        for name in ('control_finish', 'control_wait'):
            if name in offered:
                control: Dict[str, Any] = (
                    {}
                    if name == 'control_finish'
                    else dict(conditions=[dict(kind='after', seconds='60')])
                )
                return self._call(name, control)
        raise ValueError('rehearsal found no tool that ends this request')

    async def request(self, profile: Any, payload: Dict[str, Any], secret: str) -> Dict[str, Any]:
        """Return one function call with token counts estimated from the request size."""
        call = self.choose(payload)
        size = len(json.dumps(payload.get('input') or []))
        usage: Dict[str, Optional[int]] = dict(input_tokens=size // 4, output_tokens=20)
        output: List[Dict[str, Any]] = [call]
        return dict(output=output, usage=usage, model=MODEL_NAME)
