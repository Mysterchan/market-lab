"""Agent-side market tools; all reads retain the platform's visible-time cutoff."""

from __future__ import annotations

from typing import Optional
from .base import PlatformTool, ToolResponse, SYMBOL, CURSOR, LIMIT, counter, integer


class EnvironmentTool(PlatformTool):
    """Proxy for one platform tool, created per Agent client by built_in_tools()."""

    execution_route = 'market'
    name = 'environment.describe'
    description = 'Read the available tools, trading rules, units and data limits.'

    def run(self) -> ToolResponse:
        """Read the experiment's supported capabilities without querying future data."""
        return self.client.call_tool(self.name, {})


class MarketQuoteTool(PlatformTool):
    """Proxy for one platform tool, created per Agent client by built_in_tools()."""

    execution_route = 'market'
    name = 'market.quote'
    description = 'Read the currently visible best bid, ask and last trade for the stock.'
    parameters = dict(symbol=SYMBOL)

    def run(self, symbol: str) -> ToolResponse:
        """Read a quote for symbol. The result includes the actual data cutoff."""
        return self.client.call_tool(self.name, dict(symbol=symbol))


class MarketDepthTool(PlatformTool):
    """Proxy for one platform tool, created per Agent client by built_in_tools()."""

    execution_route = 'market'
    name = 'market.depth'
    description = 'Read visible bid and ask levels, up to the granted depth limit.'
    parameters = dict(symbol=SYMBOL, levels=integer('Number of bid and ask price levels.'))

    def run(self, symbol: str, levels: int) -> ToolResponse:
        """Read a bounded number of book levels for the experiment stock."""
        return self.client.call_tool(self.name, dict(symbol=symbol, levels=levels))


class MarketTradesTool(PlatformTool):
    """Proxy for one platform tool, created per Agent client by built_in_tools()."""

    execution_route = 'market'
    name = 'market.trades'
    description = 'Read a page of visible trades. Use the returned cursor for the next page.'
    parameters = dict(symbol=SYMBOL, limit=LIMIT, cursor=CURSOR)

    def run(self, symbol: str, limit: int, cursor: Optional[str] = None) -> ToolResponse:
        """Read a page of trades; omit cursor for the first page."""
        arguments = dict(symbol=symbol, limit=limit)
        if cursor is not None:
            arguments['cursor'] = cursor
        return self.client.call_tool(self.name, arguments)


class MarketBarsTool(PlatformTool):
    """Proxy for one platform tool, created per Agent client by built_in_tools()."""

    execution_route = 'market'
    name = 'market.bars'
    description = 'Read one-minute bars within a past time range. Never request future data.'
    parameters = dict(
        symbol=SYMBOL,
        start_ns=counter('Range start in session nanoseconds.'),
        end_ns=counter('Range end in session nanoseconds.'),
        interval_seconds=dict(type='string', enum=['60']),
        limit=LIMIT,
        cursor=CURSOR,
    )

    def run(
        self,
        symbol: str,
        start_ns: str,
        end_ns: str,
        interval_seconds: str,
        limit: int,
        cursor: Optional[str] = None,
    ) -> ToolResponse:
        """Read a page of one-minute bars using exact decimal-string times."""
        arguments = dict(
            symbol=symbol,
            start_ns=start_ns,
            end_ns=end_ns,
            interval_seconds=interval_seconds,
            limit=limit,
        )
        if cursor is not None:
            arguments['cursor'] = cursor
        return self.client.call_tool(self.name, arguments)
