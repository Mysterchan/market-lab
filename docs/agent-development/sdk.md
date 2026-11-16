# Write an Agent with the SDK

The SDK is the small Python package your Agent imports to communicate with Market Lab.
It supplies the Agent context, platform-tool proxies and an optional ready-made model/tool
loop. Your project can live outside the platform repository.

## Install for development

Build the matching wheel from a platform checkout:

```bash
python tools/build_agent_sdk.py --output build/agent-sdk
```

Install the resulting wheel into your Agent development environment:

```bash
python -m pip install --no-index /absolute/path/sim_platform_agent_sdk-0.1.5-py3-none-any.whl
```

The current SDK distribution is 0.1.5, separate from the App's product version.
The wheel contains public Agent classes and their client runtime, without Platform,
SQLAlchemy, Qt or the native market library. It has not been published to PyPI.
Platform environment preparation installs the matching wheel automatically for execution;
the extra development installation is for writing/running your own project locally.

## Provide the factory

Save this in `agent.py`:

```python
from sim_platform.sdk import AgentContext, TradingAgent


def create_agent(context: AgentContext):
    return TradingAgent(
        model=context.model,
        tools=context.tools.values(),
        instructions=context.settings['initial_prompt'],
        parameters=context.settings['parameters'],
    )
```

The platform calls this factory once in the Agent process when preparing an experiment.
It must return a fresh `run(task)` object for scheduled work, or a `run()` object for a
resident carrier. It may construct objects and choose tools; work, model calls and platform
tool calls are unavailable until Play. Invalid or timed-out initialization fails preparation
and closes the child resources.

`TradingAgent` is optional. Return your own implementation to replace the internal loop.
The [scheduled example](../../examples/agents/math_agent.py) and
[resident example](../../examples/agents/resident_agent.py) show both integration styles.

## Use the context

| Value | What your Agent receives |
| --- | --- |
| `context.settings` | Detached saved settings, including the prompt and parameters |
| `context.tools` | Read-only map of granted platform-tool names to callable proxies |
| `context.tool(name)` | One proxy, or `TOOL_UNAVAILABLE` when the tool is absent or not granted |
| `context.model` | Lazy model adapter; calls and network IO execute inside the Agent process |
| `context.next_task(objective)` | A resident carrier's way to wait for Runtime work before using platform capabilities |

Platform proxies supply request/work identities. Runtime owns work validity, waits, model
attempt deadlines and retry policy. The Agent owns its prompt, conversation and model IO.
A configured model is bound before Play; constructing `context.model` does not send a request
or load credentials. Custom providers must be importable in the Agent environment.

Completing work does not require trading. Finish or wait can end the current work item.
An unavailable tool is an error, rather than permission to invent a platform capability.
See [processes and tool requests](../architecture/processes.md) for the IPC route.

## Add local tools

```python
from sim_platform.sdk import Tool


class Double(Tool):
    name = 'double'
    description = 'Multiply a number by two.'
    parameters = {'value': {'type': 'number'}}

    def run(self, value: float) -> float:
        return value * 2
```

Supply `tools=[Double(), *context.tools.values()]` to `TradingAgent`. `Double` executes
inside the Agent process; it needs no platform registration or IPC. See the
[complete math example](../../examples/agents/math_agent.py).

Tool invocation checks the Python call signature and finite JSON output. Your tool validates
its own domain rules; schema descriptions guide the model but are not a full JSON Schema
validator. Results may be JSON numbers, strings, objects, arrays, booleans or null. Expected
execution/type failures produce `TOOL_EXECUTION_FAILED`; invalid output produces
`TOOL_RESULT_INVALID`. Unknown tools, duplicate names and local names impersonating platform
tools are rejected. Local tools do not complete Runtime work. Uncertain trade transport is
not automatically retried by the Agent.

### Roles inside one Agent

A multi-role Agent (analysts, a debate, a trader) runs several `TradingAgent` objects on
the same model. Give a role only the tools its job needs and a local tool that ends its
turn, named in `finish_tools`:

```python
analyst = TradingAgent(
    context.model,
    [context.tools['news.list'], SubmitReport()],
    'You are the news analyst. Report what matters for the stock.',
    finish_tools=('report.submit',),
)
report = analyst.run('Summarise the latest news.')['report']
```

The role's turn ends with that tool's result; only the trader uses `control.finish`.
Give each role a `label` (`TradingAgent(..., label='news analyst')`): its model requests
are then counted per role in the Agent log's usage. A round ends when the trader finishes,
so steps that review a decision belong at the start of the next round. The
[reference Agents](reference-agents.md) are complete examples.

## Save and run

Follow [Agent projects](projects.md) to import a file/folder, save a revision and prepare its
environment. Then select the saved revision in Desktop or call
`open_experiment(method='agent', agent_id=..., revision=...)`. Normal saved-project calls
omit `agent_factory`. Each experiment has fresh objects, conversation and a writable source
copy; dependency environments can be reused.

The SDK is an interface, not an OS security sandbox.
By default imported Python is trusted local code. Add `sandbox` to the experiment
settings to confine it: `dict(kind='auto', network='none')` runs the Agent's process
in `sandbox-exec` on macOS, or in bubblewrap or a rootless Podman container (host
filesystem read only, `--preserve-fd` for the platform's pipes) on Linux. Only the
Agent's writable source copy can be written; `network` is `none`, `local` (loopback
only, macOS) or `model` (for an Agent that calls its model itself). Docker cannot pass
the platform's open pipes into a container, so it is not supported. The sandbox wraps
only the interpreter command; results are the same with and without it.
Pause gates SDK work/model/tool delivery; arbitrary local computation is cooperative.
Stop terminates the owned process family. For supported versions and OS evidence, read
[support](../user-guide/support.md) and [verification](../development/verification.md).

[Section index](README.md) · [All documentation](../README.md)
