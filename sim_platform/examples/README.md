# Runnable platform examples

These examples use public Platform setup and project-generated synthetic data. They are
examples/fixtures, not broker adapters or a second product execution engine.

| File | Purpose |
| --- | --- |
| `demo.py` | Fixed account examples exposed by `python -m sim_platform demo` |
| `execution_demo.py` | Simulated buy/sell flow using the actual market/execution model |
| `market_demo.py` | Synthetic market source generation |
| `decision_demo.py`, `handoff_demo.py` | Rule decisions/compatible handoff and recorded financial verification |
| `fixture_executor.py` | Explicit trusted account fixture harness; ordinary policies cannot inject fills |
| `agent_reads_demo.py` | Ready Agent experiment with synthetic source and platform reads |
| `configured_agent_demo.py` | Built-in Agent loop with scripted model, actual Runtime/Tools/financial infrastructure |
| `agent_project_demo.py` | Source import, empty managed environment and reuse without model calls |

```bash
python -m sim_platform demo
python -m sim_platform execution-demo
python -m sim_platform.examples.configured_agent_demo
```

Run from the repository root after native build/dependency setup. The scripted Agent demo
needs no API key. External user projects are illustrated in [examples/agents](../../examples/agents/README.md);
read the [SDK guide](../../docs/agent-development/sdk.md) before modifying the factory or local tools.
