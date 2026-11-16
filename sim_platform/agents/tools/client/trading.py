"""Agent-side order tools. Funds, reservations and fills remain Execution's job."""

from __future__ import annotations

from typing import Optional
from .base import PlatformTool, ToolResponse, SYMBOL, CURSOR, LIMIT, text, integer

# Largest signed 64-bit integer.
MAX_INT64 = 2**63 - 1


class OrdersListTool(PlatformTool):
    """Proxy for one platform tool, created per Agent client by built_in_tools()."""

    name = 'orders.list'
    description = 'Read a page of this run\'s orders, filtered to open orders or all orders.'
    parameters = dict(limit=LIMIT, status=dict(type='string', enum=['open', 'all']), cursor=CURSOR)

    def run(self, limit: int, status: str, cursor: Optional[str] = None) -> ToolResponse:
        """List this run's orders; status is open or all."""
        arguments = dict(limit=limit, status=status)
        if cursor is not None:
            arguments['cursor'] = cursor
        return self.client.call_tool(self.name, arguments)


class OrderGetTool(PlatformTool):
    """Proxy for one platform tool, created per Agent client by built_in_tools()."""

    name = 'orders.get'
    description = 'Read one order belonging to this run, including its remaining quantity.'
    parameters = dict(order_id=text('Order ID returned by the platform.'))

    def run(self, order_id: str) -> ToolResponse:
        """Read one order's status and quantities without changing it."""
        return self.client.call_tool(self.name, dict(order_id=order_id))


class SubmitLimitOrderTool(PlatformTool):
    """Proxy for one platform tool, created per Agent client by built_in_tools()."""

    name = 'orders.submit_limit'
    category = 'trade'
    description = (
        'Submit a limit order in whole shares at a decimal USD price. It may be rejected, '
        'filled, partly filled or left open. Submitting does not finish this work round.'
    )
    parameters = dict(
        symbol=SYMBOL,
        side=dict(type='string', enum=['buy', 'sell']),
        quantity_shares=integer('Whole positive shares.', maximum=MAX_INT64),
        limit_price_usd=text('Positive USD price with up to four decimals, e.g. "100.2500".'),
    )

    def run(
        self, symbol: str, side: str, quantity_shares: int, limit_price_usd: str
    ) -> ToolResponse:
        """Submit once; the returned result reports acceptance/rejection, not guaranteed fills."""
        return self.client.call_tool(
            self.name,
            dict(
                symbol=symbol,
                side=side,
                quantity_shares=quantity_shares,
                limit_price_usd=limit_price_usd,
            ),
        )


class SubmitMarketOrderTool(PlatformTool):
    """Proxy for one platform tool, created per Agent client by built_in_tools()."""

    name = 'orders.submit_market'
    category = 'trade'
    description = (
        'Submit a market order in whole shares. It fills at the displayed prices, at most 5% '
        'beyond the best quote when accepted; any rest keeps working at that price. It may be '
        'rejected. Submitting does not finish this work round.'
    )
    parameters = dict(
        symbol=SYMBOL,
        side=dict(type='string', enum=['buy', 'sell']),
        quantity_shares=integer('Whole positive shares.', maximum=MAX_INT64),
    )

    def run(self, symbol: str, side: str, quantity_shares: int) -> ToolResponse:
        """Submit once; the returned result reports acceptance/rejection, not guaranteed fills."""
        return self.client.call_tool(
            self.name, dict(symbol=symbol, side=side, quantity_shares=quantity_shares)
        )


class CancelOrderTool(PlatformTool):
    """Proxy for one platform tool, created per Agent client by built_in_tools()."""

    name = 'orders.cancel'
    category = 'trade'
    description = 'Cancel the unfilled remainder of an order. Completed fills stay in place.'
    parameters = dict(order_id=text('Order ID returned by the platform.'))

    def run(self, order_id: str) -> ToolResponse:
        """Cancel remaining shares while retaining all completed fills."""
        return self.client.call_tool(self.name, dict(order_id=order_id))


class RequestResultTool(PlatformTool):
    """Proxy for one platform tool, created per Agent client by built_in_tools()."""

    name = 'requests.get'
    category = 'trade'
    description = 'Read a retained result by its platform request ID without placing another order.'
    parameters = dict(target_request_id=text('A previous platform request_id.'))

    def run(self, target_request_id: str) -> ToolResponse:
        """Inspect a previous result; this never resubmits its order."""
        return self.client.call_tool(self.name, dict(target_request_id=target_request_id))
