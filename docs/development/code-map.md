# Find the code for a change

Use this map after reading [how an experiment runs](../architecture/experiments.md).
Paths in the table are relative to `sim_platform/`. The [package map](../../sim_platform/README.md)
provides the next level of navigation within each component.

| Change | Start in | Preserve |
| --- | --- | --- |
| Public setup/API | `application/platform.py` (experiments, markets, Replay), `application/platform_records.py`, `platform_agents.py`, `platform_credentials.py`, `application/experiments/` | Public types/examples, root ownership and setup rollback |
| Saved Agent source | `application/agents/`, `agents/projects/`, `persistence/agents/` | Static inspection, copied revisions and profile ownership |
| Python/dependencies | `agents/runtimes/`, `agents/environments/`, `agents/preparation/` | Selected interpreter, cancellation and ready/reuse records |
| Agent loop/local tools | `sdk/`, `agents/carrier/`, `examples/agents/` | Public SDK interface and user-project boundary |
| Platform tool | `agents/tools/` client/protocol/server/read/trading components | Grants/schema, capture cutoff, route, result bounds and errors |
| Work scheduling/retry | `experiments/scheduling/` | Work/epoch identity, attempt deadlines and control gates |
| Financial behavior | `execution/`, `decisions/service.py`, `domain/ledger/` | Single writer, reservations, ordering, transaction and publication |
| Replay | `replay/`, `application/experiments/replay.py` | Correct record family, isolated destination and no model rerun |
| Desktop | `desktop/` | Platform-owned resources, command gating and visual reference |
| Shared visual specification | `design.py` | Semantic colors, type roles and spacing used by Qt, WebEngine and the documentation builder |
| Command line | `cli/` | Registration/handler separation and Platform-owned composition |
| Native input/book | `../src/`, `../include/` | Framing validation, book invariants and Python/native interfaces |

Before changing a boundary, identify its caller, executing process/thread, input/output,
state owner and failure behavior. [Processes](../architecture/processes.md) explains where
code executes; [lifecycle](../architecture/lifecycle.md) explains who must close it.
Use named runner methods and assembly contracts when extending adapters.

Follow [coding guidelines](coding.md), update the owning guide and run the relevant checks
from [verification](verification.md). Timing/capture/IPC changes also need a bounded benchmark
with its source and workload recorded. Historical timings are not evidence for a new change.

[Section index](README.md) · [All documentation](../README.md)
