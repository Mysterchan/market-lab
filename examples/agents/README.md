# Agent source examples

These files live outside `sim_platform`. They import only `sim_platform.sdk`.

- `math_agent.py`: use TradingAgent with platform tools and a local numeric tool.
- `resident_agent.py`: replace the template with an explicit loop and platform proxies.

Pass their `create_agent` callable as `Platform.open_experiment(...,
agent_factory=create_agent)`. The resident example requires a `python_carrier`
definition; the math example uses a configured or scheduled Python definition.
Saved Python package metadata is currently only validated metadata, not an import
loader. Neither example needs changes to platform tool registries or financial code.
See [SDK usage and limits](../../sim_platform/sdk/README.md).
