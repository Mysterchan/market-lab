"""Agent-side text and fundamentals tools; results never include data published later."""

from __future__ import annotations

from typing import List, Optional

from .base import PlatformTool, ToolResponse, SYMBOL, LIMIT, integer


class NewsListTool(PlatformTool):
    """Proxy for one platform tool, created per Agent client by built_in_tools()."""

    execution_route = 'market'
    name = 'news.list'
    description = 'Read the most recent news published up to the current market time, newest first.'
    parameters = dict(limit=LIMIT)

    def run(self, limit: int) -> ToolResponse:
        """Read up to ``limit`` publications that are already visible."""
        return self.client.call_tool(self.name, dict(limit=limit))


class FundamentalsTool(PlatformTool):
    """Proxy for one platform tool, created per Agent client by built_in_tools()."""

    execution_route = 'market'
    name = 'fundamentals.get'
    description = (
        'Read company fundamentals as published up to the current market time: for each '
        'metric, the latest periods, newest first. Omit metrics to list what is available.'
    )
    parameters = dict(
        symbol=SYMBOL,
        periods=integer('Number of most recent periods per metric (1-20).'),
        metrics=dict(
            type='array',
            items=dict(type='string'),
            description='Metric names, for example us-gaap:Revenues. Omit to list metrics.',
        ),
    )

    def run(self, symbol: str, periods: int, metrics: Optional[List[str]] = None) -> ToolResponse:
        """Read point-in-time values; a restatement appears only once it was published."""
        arguments = dict(symbol=symbol, periods=periods)
        if metrics is not None:
            arguments['metrics'] = list(metrics)
        return self.client.call_tool(self.name, arguments)


class DocumentsSearchTool(PlatformTool):
    """Proxy for one platform tool, created per Agent client by built_in_tools()."""

    execution_route = 'market'
    name = 'documents.search'
    description = (
        'Search filings and reports published up to the current market time. Returns the '
        'best matches with a snippet; read one in full with documents.read.'
    )
    parameters = dict(
        query=dict(type='string', description='Words to look for, such as "guidance margin".'),
        limit=integer('Maximum documents to return (1-20).', maximum=20),
    )

    def run(self, query: str, limit: int) -> ToolResponse:
        """Search visible documents; an empty query lists the newest."""
        return self.client.call_tool(self.name, dict(query=query, limit=limit))


class DocumentsReadTool(PlatformTool):
    """Proxy for one platform tool, created per Agent client by built_in_tools()."""

    execution_route = 'market'
    name = 'documents.read'
    description = 'Read one page of a visible document; pass next_offset to continue.'
    parameters = dict(
        document_id=dict(type='string', description='A document_id from documents.search.'),
        offset=integer('Character offset to start from.', minimum=0),
    )

    def run(self, document_id: str, offset: int = 0) -> ToolResponse:
        """Read up to 8,000 characters of the document from ``offset``."""
        return self.client.call_tool(self.name, dict(document_id=document_id, offset=offset))


class JournalTool(PlatformTool):
    """Proxy for one platform tool, created per Agent client by built_in_tools()."""

    name = 'journal.write'
    description = (
        'Record a note in the experiment record, such as the reason for a decision or your '
        'current beliefs. It changes nothing in the market or account.'
    )
    parameters = dict(text=dict(type='string', description='The note, at most 4,000 characters.'))

    def run(self, text: str) -> ToolResponse:
        """Append a note to this run's operation log."""
        return self.client.call_tool(self.name, dict(text=text))
