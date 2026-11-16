# Find your way around the platform

Start with [Platform](application/platform.py): the single composition entry for
opening research workspaces and prepared experiments. User Agent code imports
[sim_platform.sdk](sdk/README.md).

Choose a folder by the responsibility you want to change:

| I want to change… | Start here |
| --- | --- |
| Import/check saved Agent source | [agents/projects](agents/projects/README.md) |
| Agent Python selection/download/import | [agents/runtimes](agents/runtimes/README.md) |
| Preparation job/subprocess lifecycle | [agents/preparation](agents/preparation/README.md) |
| Dependency environment preparation | [agents/environments](agents/environments/README.md) |
| Agent project/environment storage | [persistence/agents](persistence/agents/README.md) |
| Public experiment setup and resource ownership | [application/platform.py](application/platform.py) |
| How Platform connects experiment components | [application/experiments](application/experiments/README.md) |
| Preparing choices, loading data, controlling an experiment | [research](research/README.md) |
| Human or Rule decisions, in Controlled or Live mode | [decisions/policy](decisions/policy/README.md) |
| The Agent runner that connects the platform to an Agent | [decisions/agent](decisions/agent/README.md) |
| Work wakeups, waiting, model timeout or retry policy | [experiments/scheduling](experiments/scheduling/README.md) |
| Ordering market events and commands, execution controls, result delivery | [experiments/execution](experiments/execution/README.md) |
| Starting, communicating with or stopping the Agent process | [agents/hosting](agents/hosting/README.md) |
| Agent loop, prompt, conversation or model adapter | [agents/carrier](agents/carrier/README.md) |
| Platform tool proxies, routing or result formatting | [agents/tools](agents/tools/README.md) |
| Financial validation, reservations, orders and fills | [execution](execution/README.md) |
| Historical input, preparation or time-limited market queries | [market](market/README.md) |
| Accounts, ledger values and pure domain rules | [domain](domain/README.md) |
| Database transactions, repositories or file storage | [persistence](persistence/README.md) |
| Screens and widgets | [desktop](desktop/README.md) |
| CLI commands | [cli](cli/README.md) |

Folders describe functions, not running processes. A `host.py` controls a child from
the App; `worker.py` or `process.py` contains its child entrypoint; `client.py`
contains a caller-side capability. A runner or controller is an object, not a thread.
Each process-owning package documents which side executes each file.

For setup and execution flow, read [architecture](../docs/architecture/README.md).
[Processes and threads](../docs/architecture/processes.md) identifies the running entrypoints;
[lifecycle](../docs/architecture/lifecycle.md) identifies resource owners.
Use the [code map](../docs/development/code-map.md) to plan a change and
[verification](../docs/development/verification.md) to choose its checks.

Tests live in [tests/platform](../tests/platform/README.md). Runnable examples live in
[examples](examples/README.md) and [external Agent examples](../examples/agents/README.md).
