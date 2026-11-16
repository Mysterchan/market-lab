"""Agent-side account tools; combined reads retain the financial execution cutoff."""

from __future__ import annotations

from .base import PlatformTool, ToolResponse, SYMBOL, integer


class AccountSnapshotTool(PlatformTool):
    """Proxy for one platform tool, created per Agent client by built_in_tools()."""

    name = 'account.snapshot'
    description = 'Read cash, reserved cash, holdings and available shares.'

    def run(self) -> ToolResponse:
        """Read the account, including resources reserved for open orders."""
        return self.client.call_tool(self.name, {})


class StateSnapshotTool(PlatformTool):
    """Proxy for one platform tool, created per Agent client by built_in_tools()."""

    name = 'state.snapshot'
    description = 'Read market and account data together at one execution cutoff.'
    parameters = dict(symbol=SYMBOL, order_limit=integer('Maximum orders to include.', minimum=0))

    def run(self, symbol: str, order_limit: int) -> ToolResponse:
        """Read a consistent market/account view with bounded order details."""
        return self.client.call_tool(self.name, dict(symbol=symbol, order_limit=order_limit))
