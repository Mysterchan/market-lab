"""Validation for version-one execution recovery state."""

from __future__ import annotations

from typing import Any

from sim_platform.domain.models import ContractError, integer


def validate(state: Any) -> None:
    """Raise ContractError unless ``state`` is a well-formed version-one checkpoint."""
    if not isinstance(state, dict):
        raise ContractError('invalid run checkpoint')
    try:
        for key in (
            'tick',
            'market_sequence',
            'boundary',
            'ordinal',
            'account_sequence',
            'decision_index',
        ):
            integer(state[key], 'checkpoint ' + key)
        market = state['market']
        integer(market['time_ns'], 'checkpoint time')
        if market['sequence'] != state['market_sequence']:
            raise ContractError('checkpoint market position mismatch')
        for side in ('bids', 'asks'):
            seen = set()
            for price, raw, available in state[side]:
                integer(price, 'checkpoint price', 1)
                integer(raw, 'checkpoint raw liquidity')
                integer(available, 'checkpoint available liquidity')
                if price in seen or available > raw:
                    raise ContractError('invalid checkpoint liquidity')
                seen.add(price)
    except (KeyError, TypeError, ValueError) as exc:
        raise ContractError('invalid run checkpoint') from exc
