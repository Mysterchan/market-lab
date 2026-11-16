"""A run's model budget: tokens and USD spent, checked before each model request.

Runs in the Runtime child. Usage arrives with each completed attempt (the provider's
reported token counts); a request is refused once the budget is spent, and the App
then ends the run, recorded as ended by its budget.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

# USD price fields are per this many tokens.
TOKENS_PER_PRICE = 1_000_000


class ModelBudget:
    """Spend so far against an optional ``budget`` setting (see agents.configuration).

    Args:
        budget: ``{'max_tokens', 'max_usd', 'prices'}`` or None for no limit.
    """

    def __init__(self, budget: Optional[Dict[str, Any]]) -> None:
        self.budget = budget or {}
        self.tokens = 0
        self.usd = 0.0
        self.reported = False

    def add(self, usage: Any) -> None:
        """Count one reply's usage: input plus output tokens, priced when prices are set."""
        if not isinstance(usage, dict):
            return
        counts = {
            key: usage[key] if type(usage.get(key)) is int else 0
            for key in ('input_tokens', 'output_tokens', 'cached_tokens')
        }
        self.tokens += counts['input_tokens'] + counts['output_tokens']
        prices = self.budget.get('prices')
        if prices:
            cached = min(counts['cached_tokens'], counts['input_tokens'])
            cached_price = prices.get('cached_input', prices['input'])
            self.usd += (
                (counts['input_tokens'] - cached) * prices['input']
                + cached * cached_price
                + counts['output_tokens'] * prices['output']
            ) / TOKENS_PER_PRICE

    def exceeded(self) -> Optional[Dict[str, Any]]:
        """What was spent and the limit reached, or None while within budget."""
        limit = None
        if 'max_tokens' in self.budget and self.tokens >= self.budget['max_tokens']:
            limit = 'max_tokens'
        elif 'max_usd' in self.budget and self.usd >= self.budget['max_usd']:
            limit = 'max_usd'
        if limit is None:
            return None
        return dict(
            limit=limit,
            tokens=self.tokens,
            usd=round(self.usd, 6),
            budget={k: v for k, v in self.budget.items() if k != 'prices'},
        )

    def snapshot(self) -> Dict[str, Any]:
        return dict(tokens=self.tokens, usd=round(self.usd, 6))
