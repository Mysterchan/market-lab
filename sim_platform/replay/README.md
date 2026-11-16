# Replay recorded operations

Replay executes saved trading commands against the original market events. It does
not run the user's policy, Agent program, model, scheduler or original controls.
Fills are computed by the existing execution engine; recorded fills are comparison
evidence, never instructions to the engine.

Start at `Platform.replay_prepared_operations(profile, destination_account, log_path)`.
The destination must have the same starting portfolio and order history. Playback
modifies that destination and creates a new run; it leaves the source run/account
alone. The application composes the reader and ordinary PolicyDecisionRunner in
[experiments/replay.py](../application/experiments/replay.py).

| File | Responsibility |
| --- | --- |
| `plan.py` | Validate record continuity, request ordering/outcomes and the financial endpoint; select original trading requests. |
| `commands.py` | Translate supplied Human/Rule actions or Agent trading tools to existing financial commands; remap Agent order IDs. |
| `comparison.py` | Compare final balances/orders and ordered financial events without destination-generated IDs. |
| `runner.py` | Replay/recovery session used by `Platform.replay_run()`: native raw-source runs, and Controlled runs on prepared data (including price bars), whose source it prepares again and checks against the recorded shape. |

Prepared playback does not reproduce elapsed wall time while the original was
paused, model retries, work scheduling or tool reads. It does retain actual
cancellations at the experiment's end. An admission cancelled before execution
has no trading effect. A processed request marked `financial_effect=False` also
has no new financial command to apply (for example a repeated request or an
unknown-order cancellation).

`operations` in the result contains original trading requests with receipt times
and order; `events` contains recalculated acceptance/rejection/fill/cancellation
records with `time_ns`. These are detached data for a future UI; no replay player
or extra process is introduced here. For older compatible logs that lack financial
event evidence, `events_verified=False` explicitly limits verification to final
state. Unsealed, ambiguous, inconsistent or damaged inputs are rejected.

Prepared operation Replay uses `Platform.replay_prepared_operations()` with a sealed operation log and matching destination account. Raw-source `Platform.replay_run()` / ReplaySession uses recorded decision/direct/handoff inputs and supports compatible recovery. Neither is a fallback decoder for the other. Validation, resource setup, operation application and financial verification are separate named steps in their implementations.
