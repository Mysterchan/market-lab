# Work scheduling

This is the independent Runtime process, shared by Live policy and Agent experiments.
It decides when work is due and whether a model attempt may start, retry or expire.
It does not call a model, decode the market, place orders or own account balances.

| File | Execution location | Responsibility |
| --- | --- | --- |
| `host.py` | App | ExperimentRuntimeHandle starts/reaps the child and carries commands/results. |
| `worker.py` | Runtime child | run_runtime drives policy timers, commands and notifications. |
| `client.py` | Agent or Rule child | Direct work/attempt capability through the Runtime channel. |
| `protocol.py` | Both sides | Bounded value messages and field validation. |
| `clock.py` | Shared by participants | Monotonic experiment clock; Pause excludes elapsed time. A turn-based clock moves only when advanced, by the App's TurnPacer ([pacer.py](../../application/experiments/pacer.py)). |
| `signals.py` | Parent/child lifecycle | Process-safe stop/running signals. |
| `journal.py` | App adapter | Financial publication sequence and synchronization history. |
| `policy/` | Runtime child | WorkSchedule, attempt policy, event buckets and wait validation. |

Platform injects the policy. The trusted prepared-market observation adapter remains
[application/experiments/runtime_policy.py](../../application/experiments/runtime_policy.py);
it can inspect the immutable index without handing future data to Agent code.
Core transport imports no Agent, market, financial execution, SQL, application or Qt.
The scheduling policy shares wait validation with tools; it never imports tool code.

`worker.py` separates three responsibilities. RuntimeIngress only reads bounded frames
into a queue. RuntimeInputs services the reserved control channel first, then a bounded
App batch, then the Agent channel. RuntimeLoop dispatches policy commands and publishes
replies/state. The outer loop services policy timeouts after every bounded input turn.
The batch limits are 4 control, 32 App and 2 Agent messages; queued work wakes another
turn rather than keeping the scheduler inside an unbounded drain.

Losing the Agent channel is a policy notification. Losing an App/control channel is a
Runtime failure. A late Agent disconnect after terminal state does not overwrite the
terminal reason. Business requests after termination remain invalid.

For the Agent loop itself, read [agents/carrier](../../agents/carrier/README.md).
For starting that child, read [agents/hosting](../../agents/hosting/README.md).
