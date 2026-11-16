# Agent SDK

This package owns the public Agent interface and its installable client-only distribution.
Agent authors should start with the [SDK guide](../../docs/agent-development/sdk.md).

| Code | Responsibility |
| --- | --- |
| `context.py` | Context/settings/model and granted-tool access passed to `create_agent(context)` |
| [carrier/agent.py](../agents/carrier/agent.py) | Optional model/tool loop exposed as `TradingAgent` |
| [tools/client/base.py](../agents/tools/client/base.py) | Public base for user-defined local tools |
| `distribution.py` | SDK package identity and explicit wheel source inventory |

Check actual public exports in [__init__.py](__init__.py). Runtime/client dependencies in
the wheel must stay independent of App storage, Qt and the native market engine. SDK
interface changes require a distribution version update and installed-wheel tests.
For source/environment ownership, see [Agent projects](../../docs/agent-development/projects.md).
