# Public setup and application services

Start with `from sim_platform import Platform`. [platform.py](platform.py) is the
public entry point. It owns shared storage and resources, then connects the components
below it. Controllers and runners never create another Platform.

## Find a change

| Responsibility | Location |
| --- | --- |
| Public methods, examples and return types | `platform.py`, `api_types.py` |
| Connect clocks, runners, process hosts and financial execution | [experiments/](experiments/README.md) |
| Import Agent source and prepare dependencies | [agents/](agents/README.md) |
| Operations inside a short database transaction | [use_cases/](use_cases/README.md) |
| Open transactions and publish committed financial changes | `operations.py`, `execution_operations.py` |
| Track resource roots, writer ownership and RAM execution state | `resources.py` |
| Reset, recover or close an application workspace | `lifecycle.py` |

## Experiment setup

`open_research(method=...)` returns a controller for step-by-step setup.
`open_experiment(...)` uses the same controller factories and prepares an experiment
in one call. The caller selects Controlled or Live mode explicitly.

Source preparation succeeds before a run is adopted and a financial writer is bound.
The controller owns the market and runner. The runner owns its execution flow, Runtime,
Agent or Rule host, Tools host and recorder. Failed setup releases acquired resources.
See [the experiment flow](../../docs/architecture/experiments.md).

## Storage boundary

Use cases receive a caller-owned unit of work. They do not commit, build process hosts
or expose ORM rows. The coordinator publishes financial RAM changes after SQL commit.
DatabaseOperations calls the named owning service explicitly inside each transaction;
there is no service-name search or first-match routing. ApplicationServices assembles the
available services and their dependencies for that transaction.
Tool and control records are written to JSON during execution and archived at the end.

Importing an Agent saves inspected source; it does not run the factory. Dependency
preparation is a separate cancellable job. The Agent host later runs the saved factory
with the selected interpreter. Model IO belongs to that Agent process.

For caller examples and lifecycle rules, read the [Platform API](../../docs/reference/platform-api.md).
