# Processes and threads

This map describes one prepared Agent experiment after setup. A class is not a process:
Platform, ResearchController, DecisionRunner and ExecutionSession are objects called by
other threads. See [the full flow](experiments.md) for setup and ownership.

## Child processes

| Process | Entry / host code | Responsibility |
| --- | --- | --- |
| App | [Platform](../../sim_platform/application/platform.py) and method-specific runner | Setup, financial owner, output and IPC adapters |
| Runtime child | [worker.py → run_runtime](../../sim_platform/experiments/scheduling/worker.py), [host](../../sim_platform/experiments/scheduling/host.py) | Work scheduling, event waits, model attempt policy |
| Agent child | [worker.py → run_program](../../sim_platform/agents/hosting/worker.py), [host](../../sim_platform/agents/hosting/host.py) | Factory/Agent, conversation, model network IO and local user tools |
| Tools child | [process.py → run_tools](../../sim_platform/agents/tools/worker/process.py), [host](../../sim_platform/agents/tools/worker/host.py) | Format detached market/account/financial values |

There is no separate Market or ModelProvider process. Prepared readers and the shared clock
determine market visibility. Financial execution has one App owner. Python resource trackers,
providers, Qt and user code can add OS helpers; the table counts application responsibilities.

## App execution threads

| Thread | Starts / waits on | Does |
| --- | --- | --- |
| Main/caller | CLI calls or Qt event loop | User interaction or script control |
| ResearchWorker, Desktop only | Queued Qt commands and timers | Calls Platform/controllers and publishes views |
| execution-owner | Starts on Play; execution Event and due work | Ordered financial events, commands and controls |
| experiment-output | Starts during assembly; output queue and delivery gate | Resolve deferred results, write JSON and complete replies |
| agent-ipc-ingress / agent-ipc-egress | Agent pipes / reply queue | Receive requests and send completed replies |
| agent-process-supervisor | Child exit / stop polling | Detect failure and enforce child shutdown |
| runtime-ipc-send / runtime-control-send / runtime-ipc-receive | Normal/control queues and Runtime pipe | Runtime commands, acknowledgements and notices |
| tools-ipc-send / tools-ipc-receive | Job queue and Tools pipe | Submit formatting jobs and complete their Futures |

For this source path these are ten core App background threads after Play, plus caller and
optionally Desktop worker. This is not an OS thread-count guarantee. Additional experiments,
preparation jobs, libraries or user code change the count. Before Play, execution-owner has
not started; output and IPC threads can already exist. [Desktop workflow](../../sim_platform/desktop/README.md)
supports saved Agents through Platform-owned preparation jobs.

Runtime has input/output adapter threads around its serial scheduling loop. Agent main code
waits for Play, then obtains Runtime work and executes its loop. Tools has a receive/format/reply
loop. The shared clock is not a tick thread. Model IO runs in Agent, not in App.

## One tool call

```text
Agent: call granted platform Tool
  → App ingress: runner validates and requests Runtime admission
  → immutable market read: fixed-cutoff capture
    OR mutable/financial request: financial queue and execution owner
  → Tools child: format captured values
  → App output: ordered recording and delivery
  → App egress → Agent: tool result
```

Local math tools skip this route and run in Agent. Work/attempt commands use the direct
Agent–Runtime connection. [Tool client](../../sim_platform/agents/tools/client/README.md) and
[server](../../sim_platform/agents/tools/server/README.md) document their separate sides.

A blocked Desktop worker can delay the next queued user command even when the UI renders.
A background owner can also be delayed by OS scheduling or its own financial work. Multiple
processes isolate some CPU/fault costs; they do not provide hard-real-time deadlines.

[Section index](README.md) · [All documentation](../README.md)
