# Built-in Agent template

The template connects the platform's work schedule to a task-oriented Agent:

```python
client.prepare_model()
while True:
    task = client.next_task('Evaluate current opportunities; no trade is required.')
    agent.run(task)
```

`Task.instruction` says what to do. Its notification explains why work was scheduled
now. The run/work/request identities stay inside `AgentClient`; neither the Agent
loop nor individual tools builds IPC envelopes.

## How the Agent is assembled

`ConfiguredAgentProgram.prepare()` calls a common `create_agent(context)` factory
inside the Agent process before it reports ready. The default factory returns
TradingAgent; an explicitly supplied trusted factory can return another implementation.
Factories construct objects only. Work, model calls and platform tools stay gated
until Play. The model capability is lazy, so credentials can be configured after
preparation without delaying Agent construction.

Scheduled Agents implement `run(task)`; resident carriers implement `run()` and use
`context.next_task()` themselves. Both use the same Runner, Runtime, financial path
and process host. No template inheritance is required. See the
[public SDK](../../sdk/README.md) and [external examples](../../../examples/agents/README.md).

## Reading order

- [agent.py](agent.py): `TradingAgent.run(task)`, tool dispatch, explicit completion.
- [task.py](task.py): task objective and scheduler notification, without work IDs.
- [tools/client](../tools/client/README.md): typed callable tools, grouped by domain.
- [client.py](client.py): binds active work, supplies request IDs, validates and sends
  each operation through the existing transport once.
- [model.py](model.py): Responses payloads and `ToolCall` decoding; provider details
  stay outside the Agent. Response validation still uses Runtime's format-retry budget.
- [conversation.py](conversation.py): retains system/model/reasoning items, task
  notifications and matching tool outputs; no summary or truncation.
- [program.py](program.py): factory preparation, scheduled tasks and carrier shutdown.
- [hosting/worker.py](../hosting/worker.py): actual Agent child entrypoint; prepares the carrier and waits for Play.

There is no second registry/catalog containing copies of every tool's interface.
Tool names, descriptions and parameters belong to the Tool classes. Provider schemas
are generated from these objects; required/optional fields follow their run() signatures.
Protocol validation uses the same fields, while its numeric, grant and financial checks
remain authoritative.

## Use through Platform

A saved `integration='configured'`, `carrier='platform'` agent selects this template.
Configure its model through `Platform.configure_experiment_model()` before Play.
Model binding requires `model.request` and `control.finish` grants. Grant `control.wait`
if the Agent should be able to choose waiting conditions. Without model setup the
process waits without consuming Runtime work or interfering with manual tools.

Continuous, interval and event scheduling stay in Runtime. A tool read, order submit
or plain text reply does not finish a task. `FinishWorkTool` finishes even with zero
trades; `WaitTool` waits on the existing OR conditions. Completion tools must be
called alone. Other tool calls execute in returned order.

## Behavior kept during the refactor

- Platform tools use existing IPC, validation, visible-time cutoffs and financial rules.
- Local Tool instances compute inside the Agent process and return finite JSON without IPC.
- Model retries do not rerun previous tools. Uncertain tool transport errors propagate;
  the Agent does not guess whether to resend a trade.
- Pause withholds model/tool delivery and subsequent side effects until Resume.
  Stop/EOF close the existing capabilities; late replies cannot place a new order.
- Agent failure leaves accepted orders under the existing financial lifecycle.
- History belongs to one execution in Agent RAM. New tasks retain it; a new run starts
  fresh. There is no automatic memory system, summary or conversation restart feature.
- Provider context errors and the existing 64 KiB per-model-payload bound report
  `CONTEXT_TOO_LONG`; no run-wide token allowance or silent truncation is added.

No new framework dependency, process, thread, SQL table, financial path or hash is
introduced. These are trusted local tools, not an OS sandbox for uploaded Python.

Try `python -m sim_platform.examples.configured_agent_demo` after building the native
library and installing project requirements. It runs the actual template/process pipeline
with a fake model. No API key is needed. [Verification and support scope](../../../docs/development/verification.md).
