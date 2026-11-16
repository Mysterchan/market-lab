# Platform API

`from sim_platform import Platform` is the public entry for an application or script.
Create one Platform for a workspace; it connects storage, market preparation, decision
makers and experiment controls. You do not need to assemble internal services.

This page explains how to choose an API. Exact parameters, return annotations, examples
and errors live in the Platform method docstrings: [experiments, markets and Replay](../../sim_platform/application/platform.py),
[profiles, accounts and runs](../../sim_platform/application/platform_records.py),
[Agent projects and environments](../../sim_platform/application/platform_agents.py) and
[model credentials](../../sim_platform/application/platform_credentials.py).
Use IDE help or `help(Platform.open_experiment)`. Named dictionary fields are described by
[api_types.py](../../sim_platform/application/api_types.py); returned records remain ordinary dictionaries.

## Prepare and control an experiment

1. `create_profile()` returns the ID of a group for accounts, makers and Agents.
2. `create_account()` returns an account ID with the chosen opening cash.
3. `open_experiment()` prepares market data, saves a run and connects its execution resources.
   It waits for preparation and returns a ready controller. It does not start Play.
4. Use the controller's `play()`, `pause()` and `stop()` methods, then query results.
5. Close the controller when finished. Exiting `with Platform(...)` also closes owned resources.

`open_research()` returns an empty controller when a screen needs to collect choices and
show preparation progress in steps. Use `create_experiment()` for synchronous preparation,
or `begin_prepare()` / `prepare_tick()` for incremental preparation. Both setup entries use
the same factories; [execution mode](../user-guide/execution-modes.md) is a separate choice.

### A complete local example

Build with `make all`, install core requirements and save this as a Python script.
It uses synthetic data and temporary accounts, with no model calls. Keep the main guard
so child creation is safe on platforms using process spawning.

```python
from pathlib import Path
from tempfile import TemporaryDirectory

from sim_platform import Platform
from sim_platform.examples.market_demo import write_fixture


def main():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        source, _ = write_fixture(root / 'fixture')
        with Platform(root / 'workspace') as app:
            profile = app.create_profile('Example')
            account = app.create_account(profile, 'Account', 1_000_000)
            experiment = app.open_experiment(
                profile, account, source=source, method='human',
                settings={'start_mode': 'beginning'}, execution_mode='controlled',
            )
            try:
                experiment.step()
                experiment.stop()
                print(app.run(profile, experiment.run_id)['status'])
                print(app.snapshot(profile, account))
            finally:
                experiment.close()


if __name__ == '__main__':
    main()
```

Amounts and prices are integers in units of USD 0.0001: `10_000` is USD 1.
Quantities are shares; `_ns` values are session nanoseconds. Profile IDs identify user
data groups, not model settings or login sessions.

## Return values and state

| Return kind | Examples | How to use it |
| --- | --- | --- |
| ID string | `create_profile()`, `create_account()`, run creation methods | Save the ID and pass it to later calls |
| Detached data | `agents()`, `run()`, `report_data()`, `snapshot()` | A value from that read; it does not update itself. Account snapshots are frozen dataclasses |
| Owned resource | Experiment controllers, market readers, background jobs | Perform later operations and close the resource |

An empty controller has no run yet. A saved Agent draft may say `prepared` while still
requiring runtime binding. An opened experiment is ready before Play; its saved run status
can already be `running`. That status also includes Pause, so it is not a clock indicator.
Background execution tracks its own ready/running/paused/terminal state.
`Platform.closed` means shutdown has started, not that every failed cleanup has succeeded;
handle close errors and retry as documented by the resource.

## Save an Agent before selecting it

| Task | Methods | Guide |
| --- | --- | --- |
| Save a configured template | `create_agent()`, `agents()`, revision methods | [Projects and configuration](../agent-development/projects.md) |
| Import your file/folder | `check_agent_project()`, `import_agent()`, `agent_project()`, `agent_project_file()` | [Agent projects](../agent-development/projects.md) |
| Prepare Python/dependencies | `prepare_agent_environment()`, `agent_environment()`, `prepare_python_runtime()`, `python_runtimes()` | [Python selection](../agent-development/python.md) |
| Export development SDK | `export_agent_sdk()` | [SDK](../agent-development/sdk.md) |
| Select a saved revision | `open_experiment(method='agent', agent_id=..., revision=...)` | [Experiment flow](../architecture/experiments.md) |
| Several symbols in one experiment | `open_experiment(source=[...], settings=dict(symbols=[...]))`, `experiment.order(..., symbol=...)` | [Several symbols](../user-guide/several-symbols.md) |
| Market orders, short selling | `experiment.order(..., order_type='market')`, `open_experiment(..., allow_short=True)` | [Orders](orders.md) |
| Trade with your own model file | `open_experiment(method='model', settings=dict(model_file=...))`, `prepare_model_environment()`, `model_environment()` | [Your own model](../user-guide/model-policies.md) |
| Evaluate Agents against baselines | `evaluation()`, `disguise_recording()`, `open_batch().evaluate_suite()` | [Evaluation](../user-guide/evaluation.md) |
| Local model, budget, lineage | `ModelProfile('openai-compatible', ..., base_url=...)`, `settings['budget']`, `run_lineage()`, `verify_run()` | [Research controls](../user-guide/research-controls.md) |
| Explain, sweep and review | `decision_timeline()`, `pnl_attribution()`, `run_sweep()`, `diff_runs()`, `model_risk_report()` | [Study tools](../user-guide/study-tools.md) |

Import statically inspects and copies source without running it. Preparing dependencies
creates a reusable environment. Opening an experiment loads the saved revision into a new
Agent child and initializes its factory before Play. Normal saved-project calls omit
`agent_factory`. Each execution gets fresh objects and a writable source copy.

The advanced `agent_factory` hook accepts a trusted top-level callable for Python/test
composition. It uses the same host and receives `AgentContext` in the child. It is not
serialized into saved Agent JSON; use source import for reusable projects.

Model setup is separate: `set_model_credential()` / `load_model_credential()` provide a
credential reference, `check_model_credential()` confirms that a previously saved one is
available, and `configure_experiment_model()` binds a prepared experiment before
Play. Its model `profile` is a `ModelProfile`, not a user profile ID. Configured Agents need
the appropriate `model.request` and completion grants. See the method examples for the exact
model setup fields. Never put a secret into saved Agent parameters.

## Results and Replay

`snapshot()` and `events()` read account state/history. `run()` reads a saved run;
`report_data()` and `report_stream()` expose reports. Consume report iterators inside their
context manager; they belong to its read transaction.

`replay_prepared_operations()` verifies a complete recorded-operation log against an
appropriate destination account. It preserves the original account/run, creates a new run,
and does not call the policy/model or reenact UI Pause/Resume. Inspect `events_verified`:
older compatible logs may lack evidence for event-by-event comparison.

`replay_run()` handles supported raw-source records and finished Controlled experiments on
prepared data, including price-bar files; the latter prepare the original file again, which
must be unchanged ([price bars](price-bars.md#replay)). Normal verification returns a dictionary
and closes isolated resources. `replay_run(recover=True)` returns a paused `RecoveredRun`
that still owns resources; close it or use its context manager.

## Lower-level assembly

Use these only when deliberately assembling components: `create_execution_run()` with
`execution_session()`, or `create_decision_run()` with `decision_runner()`. They borrow the
caller's market, so close the executor before the market. A plain `decision_session()`
records intent/state; an intent record alone does not prove acceptance or a fill.

`prepare_agent_experiment()` saves an Agent draft; it does not prepare processes or start
execution. Do not mix these lower-level setup steps into normal `open_experiment()` calls.
Ownership, timing and shutdown are explained in [architecture](../architecture/README.md).

[Section index](README.md) · [All documentation](../README.md)
