# Financial execution

This package owns simulated order acceptance, reservations, fills and account execution.
It does not contain the work scheduler, Agent loop or model IO.

| File | Responsibility |
| --- | --- |
| `session.py` | ExecutionSession binds one run/account/borrowed market and execution ownership; controls its financial lifetime |
| `coordinator.py` | Serialize changes, call injected transactions and publish RAM after successful commit; read fast paths |
| `service.py` | Transaction-bound order lifecycle, authority/position checks, audit and checkpoint persistence |
| `publication.py` | Build initial/incremental runtime read values inside the transaction; never write events or publish RAM |
| `engine.py` | Market/order advancement without SQL or UI |
| `risk.py` | Funds/share availability and reservation rules, market-order protection prices and the short-selling margin rule ([orders](../../docs/reference/orders.md)) |
| `liquidity.py` | Visible-depth simulation supply budgets |
| `state.py` | Owner-held runtime state and publication values |
| `position.py` | Operation/source position validation shared with decisions/Replay |
| `models.py` | Execution settings and result/snapshot values |

To follow a financial change, start at Coordinator's `_write()`, then the selected
Service method. `context()` returns named transaction inputs (`run`, `config`,
`account`, `state`), not a positional tuple. The Service loads those inputs and
applies the operation. Its `complete_change()` saves the execution frontier and
asks `publication.py` for the updated view. Coordinator installs that view in
ExecutionState only after the transaction succeeds.

The publication builder keeps event/decision summaries bounded and reuses unchanged
history. It shares open-order acceptance lookup with market advancement. Its values
are read models, not a second order authority or an extra persistence mechanism.

ExecutionSession differs from [ExperimentSession](../experiments/execution/README.md), which
coordinates the background financial timeline, controls and output. There is one financial
owner, not two independent trading engines. Human, Rule and Agent commands share
Coordinator → decisions/service → execution/service → ledger.

Financial operations save accepted changes atomically. A checkpoint saves the current
financial processing position and state; it is separate from the individual order/fill
records. Idle progress uses a less frequent
checkpoint; this does not delay accepted order/fill persistence. The current periodic idle
checkpoint is eligible after 30 seconds of monotonic elapsed time and occurs on a subsequent
market event; it is not a separate fixed timer. Pause/end save along their explicit paths.

An unknown financial-storage failure ends trusted processing; it does not initiate another
speculative cancellation transaction. Normal Stop/source end cancel remaining quantities
and retain fills; Agent failure alone keeps accepted orders.

Start at [Platform API](../../docs/reference/platform-api.md) for normal creation. The lower-level
`create_execution_run()`/`execution_session()` methods support explicit execution-only use.
See [architecture](../../docs/architecture/experiments.md) and [verification](../../docs/development/verification.md).
