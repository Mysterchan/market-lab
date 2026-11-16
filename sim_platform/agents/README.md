# Agent configuration and execution

User-written Agent source starts with the [public SDK](../sdk/README.md). It can use
the template or replace its loop, and add local tools inside its own project.

- `projects/`: static inspection of captured source; no import/evaluation.
- `runtimes/`: Agent interpreter selection/provisioning, not the scheduling Runtime process.
- `preparation/`: shared background job and trusted subprocess lifecycle.
- `environments/`: cancellable dependency preparation, separate from live experiment processes.
- `configuration.py`: saved Agent and experiment configuration validation.
- `carrier/`: TradingAgent, task, exact conversation history and model adapter;
  this code executes inside the Agent process.
- `hosting/`: App host, child entrypoint and communication, with explicit sides.
- `tools/`: Agent proxies, App routing, Tools child and request/result contracts.
- `operation_log.py`: ordered Agent activity recording used by App output.

The App's AgentDecisionRunner is in [decisions/agent](../decisions/agent/README.md).
The separate work Runtime is in [experiments/scheduling](../experiments/scheduling/README.md).
Shared financial sequencing/controls/output are in
[experiments/execution](../experiments/execution/README.md).

Saving an Agent definition does not start its program. Opening an experiment prepares
a new Agent instance in its child before Play; Play opens execution capabilities.
The clock and prepared reader determine visible market data independently of Agent
model IO. There is no Market process or App model-service thread.
