# Experiment lifetime and shutdown

An experiment has several independent confirmations. A financial terminal state does
not by itself mean that its output is archived or all children have exited.

| Owner | State/resource it controls | Confirmation |
| --- | --- | --- |
| Platform | Registered controllers and shared stores | Roots close before database/workspace |
| ResearchController | Runner and borrowed market lifetime | Runner closes before market is released |
| Method-specific DecisionRunner | Agent/Rule host, Tools, Runtime and recorder | `closed` becomes true after resource release succeeds |
| ExperimentControls | Play/Pause/Stop coordination | Runtime/execution/participant acknowledgements, then resource-close confirmation |
| ExperimentSession | Financial event/command timeline | `ready → running → pausing → paused`; terminal via `ending` |
| Financial ExecutionSession | Writer lease, RAM account execution and financial commits | Financial finish or explicit abandonment of untrusted execution |
| ResultDelivery | Ordered JSON/results and delivery gate | End Future completes after ordered output has drained, or reports output failure |
| Process hosts | Actual child/IPC lifetime | Close confirms exit/drain; a failed close may be retried |

## Controls

Play starts/resumes the clock and background execution, then participant capability gates.
Pause freezes command admission/time and waits for execution, Runtime and delivery gates.
It is not an OS suspension of arbitrary Python code. Stop closes admission, invalidates
unstarted commands, finishes the financial timeline and drains output. Accepted fills are
retained; unfilled quantities are cancelled by financial termination. Agent failure alone
does not stop the market or cancel accepted orders.

The control barrier closes Runtime during normal Stop. Runners also check Runtime's close
confirmation when releasing resources, because setup failure or a concurrent terminal event
can bypass that normal path. A confirmed Runtime close is not repeated. Hosts/recorders are
released in their established sequence; unsuccessful release retains runner/root ownership.
Do not close the borrowed market while an executor may still use it.

## Failures and deadlines

ExperimentSession keeps the initiating failure in `error`; later failures while stopping
participants or finishing finance appear separately in `cleanup_errors`. This preserves the
cause without pretending that cleanup succeeded. Unknown financial-storage errors do not
initiate a second speculative transaction to cancel the account.

`CONTROL_ACK_TIMEOUT_SECONDS` and `EXECUTION_DRAIN_TIMEOUT_SECONDS` name internal coordination
bounds in [controls.py](../../sim_platform/experiments/execution/controls.py). They are not
the user-configured model timeout or a promise about total shutdown time across all phases.
Runtime's work/model attempt policy and provider network timeout retain separate owners.

Read [ExperimentSession](../../sim_platform/experiments/execution/session.py),
[Agent runner](../../sim_platform/decisions/agent/runner.py) and
[Live Policy runner](../../sim_platform/decisions/policy/live/runner.py) for the corresponding
close/error paths. Tests cover delayed output, process failure, failed close/retry and
initial failure followed by financial-cleanup failure. See [verification](../development/verification.md).

[Section index](README.md) · [All documentation](../README.md)
