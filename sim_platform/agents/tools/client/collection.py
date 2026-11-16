"""The built-in tool classes, shared by client assembly and protocol field checks."""

from __future__ import annotations

from typing import TYPE_CHECKING, Dict, List, Type

from .market import (
    EnvironmentTool,
    MarketQuoteTool,
    MarketDepthTool,
    MarketTradesTool,
    MarketBarsTool,
)
from .account import AccountSnapshotTool, StateSnapshotTool
from .trading import (
    OrdersListTool,
    OrderGetTool,
    SubmitLimitOrderTool,
    SubmitMarketOrderTool,
    CancelOrderTool,
    RequestResultTool,
)
from .control import FinishWorkTool, WaitTool
from .research import (
    DocumentsReadTool,
    DocumentsSearchTool,
    FundamentalsTool,
    JournalTool,
    NewsListTool,
)

if TYPE_CHECKING:  # Annotation only.
    from sim_platform.agents.carrier.client import AgentClient
    from .base import PlatformTool

TOOL_TYPES: Dict[str, Type[PlatformTool]] = {
    tool.name: tool
    for tool in (
        EnvironmentTool,
        MarketQuoteTool,
        MarketDepthTool,
        MarketTradesTool,
        MarketBarsTool,
        AccountSnapshotTool,
        StateSnapshotTool,
        OrdersListTool,
        OrderGetTool,
        SubmitLimitOrderTool,
        SubmitMarketOrderTool,
        CancelOrderTool,
        RequestResultTool,
        FinishWorkTool,
        WaitTool,
        NewsListTool,
        FundamentalsTool,
        DocumentsSearchTool,
        DocumentsReadTool,
        JournalTool,
    )
}


def built_in_tools(client: AgentClient) -> List[PlatformTool]:
    """Create only granted tool objects, plus the always-available environment tool."""
    return [
        tool(client)
        for name, tool in sorted(TOOL_TYPES.items())
        if name == 'environment.describe' or client.allows(name)
    ]
