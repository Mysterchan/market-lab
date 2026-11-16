# Ordered experiment execution

This package runs the shared background flow for Live Human/Rule and Agent
experiments. It does not contain a Market process or the Agent decision loop.

| File | Executes where | Responsibility |
| --- | --- | --- |
| `session.py` | App owner thread and command ingress | ExperimentSession admits commands, tracks lifecycle and processes the financial timeline. |
| `timeline/` | App owner and ingress | Sequencer fixes receipt time/order; ExecutionTimeline merges market events and commands. |
| `market.py` | App owner | LocalMarketExecution borrows the financial session; ExecutionCommands delegates the existing transaction. |
| `controls.py` | App caller | Coordinate clock, Runtime and participant Play/Pause/Stop acknowledgements. |
| `delivery.py` | App output thread | Resolve deferred results, record JSON and deliver ordered replies. |
| `results.py` | Shared value/async boundary | DeferredResult and result-slot behavior. |
| `metrics.py` | App | Measurements of processing lag and admitted work. |

ExperimentSession is held by the method-specific runner. Its owner thread starts
on Play; output starts during preparation. Financial effects use the existing
Coordinator → Service → ledger path. No alternative submit/cancel implementation
or tool-receipt SQL is introduced.

The financial ExecutionSession remains in [execution/session.py](../../execution/session.py).
It owns the run's financial execution, while ExperimentSession here coordinates
the background event flow. Market visibility comes from the shared clock and
immutable prepared reader. Market reads bypass the financial queue; account/order
reads use the owner for consistent mutable state. Tools formatting runs in its child.
