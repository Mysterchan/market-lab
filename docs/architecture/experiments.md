# How an experiment runs

An experiment connects a saved decision maker, simulated account, historical source and
execution settings. `Platform` is the public setup entry. Controllers, runners and hosts
are objects with specific responsibilities; they are not automatically threads.

## Before Play

`app.open_research(method=...)` creates an initially empty controller. The caller prepares
choices and calls `controller.create_experiment(...)`. `app.open_experiment(...)` performs
these steps in one call. Both use the same controller factories; they do not select different
backends. Execution mode is explicit. See [modes](../user-guide/execution-modes.md).

| Step | What happens | Code |
| --- | --- | --- |
| Resolve a saved Agent | Read its revision and private source reference; resolve a ready interpreter/environment when required | [Agent project application](../../sim_platform/application/agents/projects.py), [projects](../../sim_platform/agents/projects/README.md) |
| Prepare market data | Decode/validate the source and build or reuse immutable prepared data; choose the start point before adopting a run | [Prepared market](../../sim_platform/market/prepared/README.md), [ResearchController](../../sim_platform/research/controller.py) |
| Create a run | Save settings and initial account bindings in the existing short transaction path | [Agent run operations](../../sim_platform/application/use_cases/agent_runs.py), [Policy controller](../../sim_platform/research/policy_controller.py) |
| Bind financial execution | Bind the run/account/market and acquire writer ownership; keep current execution state in RAM | [ExecutionSession](../../sim_platform/execution/session.py), [Coordinator](../../sim_platform/execution/coordinator.py) |
| Assemble background participants | Connect clock, Runtime, ExperimentSession, output, Tools and Agent host | [Agent assembly](../../sim_platform/application/experiments/agent.py), [Live Policy assembly](../../sim_platform/application/experiments/factory.py) |
| Prepare the Agent | The child loads the factory and calls `create_agent(context)`; report ready and wait for Play | [loader](../../sim_platform/agents/hosting/loader.py), [carrier program](../../sim_platform/agents/carrier/program.py) |

Preparing dependencies is a separate job before this flow. Importing an Agent saves source;
it does not execute the factory. A configured model is lazy during construction and must
be bound before model-backed work. Desktop prepares through Platform-owned jobs and adopts
the candidate controller on success; a widget does not assemble its own pipeline.

## After Play: the background Agent path

```text
Caller / Desktop worker
  → controller.play() → AgentDecisionRunner → ExperimentControls
                                       │
App process                            ├─ Runtime child: work, waits, attempt policy
  execution-owner thread               ├─ Agent child: loop, context, model IO, local tools
    ordered events and commands        └─ Tools child: format captured values
  experiment-output thread
    ordered JSON and reply delivery
```

The shared clock derives experimental time from elapsed monotonic time while running.
Immutable readers expose only data visible at the captured cutoff. There is no Market
process rebuilding the book during playback, or thread incrementing the clock each millisecond.
The financial owner has its own processed frontier, which can lag visible market time.

Runtime schedules one work item at a time. The Agent waits via its direct Runtime client;
`next_work()` returns when work is available. The Agent may perform multiple model/tool
steps within that work. Finish or wait ends work without requiring a trade. Runtime owns
attempt deadlines/retry policy; the Agent performs network IO and prompt/conversation work.

## Tool requests

| Operation | Route and state owner |
| --- | --- |
| Local user tool | Runs inside the Agent project/process; never registers as a platform operation |
| Immutable market query | Agent pipe → App ingress → runner validation/Runtime admission → fixed-cutoff reader capture → Tools child → ordered output → Agent |
| Account/order query | Same ingress/admission, then financial owner captures mutable account state before Tools formats it |
| Submit/cancel | Owner's Sequencer fixes accepted receipt time/order; timeline merges due market events and commands; Commands → Coordinator → Service → ledger performs the financial transaction |

The execution position is not the time the Agent first looked at the market. Time spent
analysing/sending is included before command admission. Equal-time market events precede
commands. Accepted orders that do not fill immediately remain in financial state and can
fill on later ordered market events. Tools formatting neither reserves money nor decides fills.

Validation is layered: JSON/envelope/grants/argument checks at ingress; work/epoch/admission
in Runtime; authority, position, cash/shares and order rules in financial execution; detached
result bounds at the transport/output boundaries. See [data and tool contract](market-data.md).

## Follow one Agent from setup to an order

1. Your script or Desktop worker calls `Platform.open_experiment(...)`. It returns
   an AgentResearchController. The controller holds the prepared market and an
   AgentDecisionRunner. The runner holds the hosts and background execution session.
   Agent construction has finished, but trading work has not started.
2. You call `controller.play()`. The control barrier opens the clock and participant
   gates. Runtime can now dispatch work. The Agent's `context.next_task(...)` waits
   on its direct Runtime connection until that work is available.
3. The Agent calls `context.tool('market.quote').run(symbol='AAPL')`. Its proxy adds
   the current run/work/request IDs and writes to the App pipe. The App ingress
   thread calls `runner.request_tool()`. Runtime checks that this work is active.
4. For this immutable market query, the App captures the reader's visible cutoff.
   The Tools child formats the captured values. The App output stage records the
   result, then the egress thread returns it to the Agent. No financial command
   or book rebuild is needed to read that prepared state.
5. The Agent analyses the quote or calls its model inside the Agent process. The
   experiment clock continues. Suppose it read at 09:30:01.000 and its buy command
   reaches the Sequencer at 09:30:01.120. The command uses the later receipt position,
   not the old quote time. The financial owner processes earlier market events first,
   validates funds/reservations and applies the shared submit transaction.
6. An accepted order may remain unfilled. The financial owner checks it against later
   ordered market events. The Tools child formats the resulting account/order values;
   it does not decide whether an order fills. The Agent may inspect the order, wait,
   finish its work without trading again, or continue analysing.

The caller, runner and financial session are objects, not extra processes. A pending
reply does not stop the clock, and a current market quote does not prove the financial
owner has finished all earlier work. Use [process ownership](processes.md) to find the
thread that executes a step and [lifetime states](lifecycle.md) to interpret completion.

## Pause, Stop, failure and close

Pause freezes time and confirms participant capability gates. A reply may have arrived
while delivery is paused; this does not mean arbitrary uploaded code is frozen by the OS.
Stop closes admission, cancels unstarted requests and remaining order quantities, retaining
fills. A started financial commit is not interrupted halfway. Natural source end settles
allowed remaining events and cancels outstanding quantities. Agent failure alone retains
accepted orders; it is different from stopping the entire experiment.

Financial end, output drain/archive and resource close are separate confirmations. A failed
cleanup can retain ownership for retry. Unknown financial-storage failures do not trigger
another speculative cancellation transaction. See [controls](../../sim_platform/experiments/execution/controls.py),
[session](../../sim_platform/experiments/execution/session.py) and [runner close](../../sim_platform/decisions/agent/runner.py).

The [lifetime matrix](lifecycle.md) lists each confirmation, owner and failure path.

## Human/Rule and Replay

Controlled Human/Rule experiments use the synchronous PolicyDecisionRunner and bounded
poll/step. Live Human/Rule use the shared background execution/Runtime; a Rule computation
uses a child host. Both retain the same financial core as Agent commands.

Prepared-operation Replay applies recorded accepted operations at their recorded positions
and verifies the financial result, without calling the Agent/model or replaying UI clicks.
The older file-source Replay API additionally supports compatible recorded decision/handoff
records and recovery. They have different input requirements; consult the separate methods
in [Platform API](../reference/platform-api.md), not an inferred fallback between record formats.

[Section index](README.md) · [All documentation](../README.md)
