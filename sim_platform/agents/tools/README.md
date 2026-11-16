# Agent tools

Choose a subfolder by which part of a tool you want to change:

| Folder | Runs where | Responsibility |
| --- | --- | --- |
| `client/` | Agent child | Typed platform proxies and local Tool interface. User local implementations stay in user projects. |
| `protocol/` | Both sides | Tool request/result values, bounds, permissions and errors. |
| `server/` | App | AgentTools validates/routes operations, captures trusted state and tracks result delivery. |
| `worker/` | App host + Tools child | host.py owns the child; process.py contains run_tools, the child entrypoint. |
| `reads/` | App capture + Tools child formatting | CapturedRead values, formatting, paging and history slices. |
| `trading/` | App financial routing + detached formatting | Convert trading tools to existing financial commands and format captured results. |

Tool classes declare `execution_route`: immutable market reads use the market path;
account/state/trade/control operations retain the financial owner path. This is separate
from read/trade/control category and is the shared source of route selection.

## Paths

Local user tool: Agent → local Tool.run() → local JSON value. No IPC or App registry.

Platform market read: proxy → App Runner → submit_read fixes visible cutoff →
prepared capture → Tools child formatting → App ordered output → Agent.

Trading tool: proxy → App Runner → sequenced admission → App execution owner →
existing financial command/transaction → captured result → Tools child formatting →
App ordered output → Agent. Account/order reads also use the owner for consistent
mutable financial state. Finish/wait route to scheduling controls.

The Tools child does not own the database, order book, account, risk checks or fills.
The shared owner and financial session remain responsible for those decisions.

Read [server](server/README.md), [worker](worker/README.md) or the
[public SDK](../../sdk/README.md). To add a user math tool, edit the user Agent project,
not this folder or its platform registry.
