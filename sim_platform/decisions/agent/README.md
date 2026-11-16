# Agent decision runner

`runner.py` contains AgentDecisionRunner. It is an App object holding the shared
execution pipeline, platform tool router, recorder, Agent host and lifecycle controls.
It validates/admit tools and routes market reads separately from ordered financial
operations. It does not run the Agent's model loop or own a new scheduler thread.

- Agent child lifecycle: [agents/hosting](../../agents/hosting/README.md).
- Agent implementation: [agents/carrier](../../agents/carrier/README.md).
- Work scheduler: [experiments/scheduling](../../experiments/scheduling/README.md).
- Financial event owner: [experiments/execution](../../experiments/execution/README.md).

Platform connects these in application/experiments/agent.py. The research controller
holds this runner and calls its lifecycle methods; neither constructs Platform.
