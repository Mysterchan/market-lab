# Agent development

Your project supplies the Agent's behavior. Market Lab supplies experiment time, market
access, simulated trading, recording and process management. You do not construct those
components yourself.

```text
Write create_agent(context)
  → import a file or folder to save a revision
  → prepare its Python and dependencies
  → select that revision in an experiment
  → Play creates work for the prepared Agent
```

| Step | Guide |
| --- | --- |
| Write the factory, loop and local tools | [SDK and Agent interface](sdk.md) |
| Save source, configure capabilities and prepare dependencies | [Agent projects](projects.md) |
| Select or provision a compatible Python | [Python selection](python.md) |
| Start from a published architecture (TradingAgents, FinMem, FinCon) | [Reference Agents](reference-agents.md) |

Use the built-in template to start quickly, or return your own `run(task)` / `run()` object.
Examples live in [examples/agents](../../examples/agents/README.md).
Platform tool proxies come from `context`; your own tools run inside the Agent process.
For the underlying communication route, see [processes and tools](../architecture/processes.md).

[All documentation](../README.md)
