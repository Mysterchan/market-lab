# Importing and preparing an Agent

The public entry is Platform. Write your Agent using `sim_platform.sdk`, then import
a Python file or folder. Your code does not need to construct a Controller, Runtime,
Runner, IPC client or platform tool registry.

## Project format

A single Python file is copied as `agent.py`. A folder uses:

```text
my_agent/
  agent.py                  required: create_agent(context)
  tools/math.py             optional: your local tools/helpers
  requirements.txt          optional: dependency names and version constraints
  agent.json                optional: {"python": "3.12"}
```

The factory must be one undecorated synchronous top-level function accepting one
context argument. It returns a scheduled `run(task)` Agent or a resident `run()`
Agent according to the saved integration. Static inspection does not execute it,
prove imports are available, or verify the returned object's runtime behavior.
Those initialization checks happen inside the selected Agent process when an experiment is opened.

`requirements.txt` accepts entries such as `numpy==1.26.4` or `example>=1,<2`, extras
and comments. Direct URLs, local/VCS paths, editable packages, pip options/includes
and environment markers are rejected. The file is the dependency source; pyproject
build/install hooks are never run. Transitive dependencies must also resolve to wheels.
Agent Python is selected independently of the App. A new project defaults to 3.12;
minor, exact patch and range requests are supported. See [runtime preparation](python.md)
for downloads, offline archives and the supported provisioning policy.

Supported project files are UTF-8 .py/.json/.txt/.md/.toml/.csv/.yaml/.yml. Limits are
500 files/inventory entries, 5 MiB per file, 50 MiB of captured text and 16 nested
folders. Symlinks and special files are rejected. Generated directories such as
.git/.venv/__pycache__/build/dist are skipped. .env/.env.*, known credentials/secrets
files and private-key extensions are excluded and listed by path, not copied.
Credentials belong in the separate model credential setup. Do not embed keys in
source or arbitrary asset files: this importer is not a general secret detector.

## Save and inspect

```python
from sim_platform import Platform

with Platform('/absolute/path/app-data') as app:
    profile_id = app.create_profile('Research')
    checked = app.check_agent_project('/absolute/path/my_agent')
    saved = app.import_agent(
        profile_id, '/absolute/path/my_agent', name='My Agent',
        integration='python_strategy',
        parameters={'risk_preference': 'user choice'},
    )
    agent_id = saved['agent_id']
    project = app.agent_project(profile_id, agent_id)
    source = app.agent_project_file(profile_id, agent_id, 'agent.py')
```

`python_strategy` supplies `run(task)` to platform scheduling; the default schedule
is a 30-second interval. Pass schedule explicitly to change it. Use python_carrier
for resident `run()` and omit schedule. Default required capabilities are market.quote,
control.finish and model.request; pass requires explicitly for another set. Local
math tools are normal project code and are not platform capability names.

The platform generates Agent/project identities and stores the copied source reference.
Configured templates use `create_agent()`; imported projects use `import_agent()`.
Both appear in the same `agents()` list.

To update, edit the original source and import again:

```python
updated = app.import_agent(
    profile_id, '/absolute/path/my_agent', name='My Agent',
    agent_id=agent_id, expected_revision=saved['revision'],
)
old_source = app.agent_project_file(profile_id, agent_id, 'agent.py', revision=1)
```

Every import captures full choices for the new revision. Pass prompt, parameters,
requirements/grants and schedule choices that you want on that revision; it is not
a partial settings patch. A stale revision is rejected and its new candidate is
removed. The original file can move/change after import without changing saved source.
Earlier experiments/revisions remain unchanged. Cross-profile reads are refused,
including a definition with another profile's manually copied project_ref.

## Prepare dependencies

```python
job = app.prepare_agent_environment(profile_id, agent_id)
try:
    status = job.wait(30)  # May still be preparing when this timeout expires.
    print(status)
finally:
    job.close()          # Cancel unfinished work and confirm cleanup.
```

With no dependencies this creates a clean managed venv. For dependencies, choose
an offline directory containing only prebuilt wheels:

```python
job = app.prepare_agent_environment(profile_id, agent_id,
                                    wheelhouse='/absolute/path/wheels')
```

Or explicitly enable public package downloads, starting from PyPI:

```python
job = app.prepare_agent_environment(profile_id, agent_id, allow_download=True)
```

Downloads use wheel-only resolution. Wheels are inspected before installation;
traversal, symlink entries, excessive expansion, .pth/sitecustomize/usercustomize,
pip replacement and direct-URL dependency metadata are rejected. No user module,
source build or installed dependency is imported as a readiness probe. The wheel
inventory is saved with the environment. This is not an OS sandbox.

snapshot() returns:

```text
state: preparing | ready | failed | cancelled
phase: waiting | resolving | creating_environment | installing | ready | failed | cancelled
environment: null, or {environment_ref, specification, packages, state: ready}
reused: true/false
error: null, or {code, message}
```

Phases are progress indicators, not a byte percentage or reliable ETA. cancel() is
nonblocking. close() waits for the worker and subprocess cleanup; Platform also owns
and closes jobs. Each subprocess is bounded to 300 seconds and 1 MiB captured output.
Cancellation terminates its process group. At most one environment preparation holds
the cross-instance lease; waiting jobs can be cancelled. Identical dependency and
interpreter specifications reuse one ready environment, including across Agent
source revisions and Platform reopen. There is no database transaction during install.

A failed/cancelled candidate is removed without replacing earlier ready environments.
Read readiness later using `app.agent_environment(profile_id, agent_id, revision)`.
Named failures include INVALID_DEPENDENCY, INVALID_ENTRYPOINT, PYTHON_UNAVAILABLE,
DEPENDENCY_SOURCE_REQUIRED, DEPENDENCY_UNAVAILABLE, UNSAFE_DEPENDENCY,
PREPARATION_TIMEOUT and CLEANUP_FAILED. Failure is visible in the job snapshot;
wait() does not raise installation errors.

## Run a saved Agent

Preparation installs the matching platform SDK as well as dependencies. Once ready,
select agent_id and revision through the normal open_experiment()/open_research() path;
no explicit factory or platform-code edits are required. Initialization happens before
Play inside the selected Python. Each run gets a writable source copy and fresh objects;
that copy is removed after successful close. Saved revisions stay independent.
See [development, SDK installation and run examples](sdk.md).

### News, fundamentals, macro data and filings

These read tools give an Agent text, accounting and economic data without letting it see
the future. Grant them in `requires` like any platform capability:

| Tool | Returns |
| --- | --- |
| `news.list` | The newest publications of the recording's news file whose `available_time_ns` has passed |
| `fundamentals.get` | For each requested metric, the latest periods published by the current market time |
| `documents.search` | Filings and reports published so far that match a query, with a snippet |
| `documents.read` | One 8,000-character page of a visible document |
| `journal.write` | Records a note (a reason, a belief) in the run's log; read with `agent_journal()` |

Attach fundamentals to a run with the experiment setting `fundamentals_path`: a CSV with
columns `symbol, metric, period_end, value, available_date` (optional `unit`, `form`), or
an SEC EDGAR `companyfacts` JSON file you downloaded (metrics such as `us-gaap:Revenues`).
`available_date` is when the figure became public, never the period end. A figure counts as
public from the start of the next day, and a later restatement stays hidden until its
own filing date. The recording must carry dates (a price-bar file), because publication
dates are placed on its timeline. Without a file, `fundamentals.get` reports
`DATA_UNAVAILABLE`. From Python, `sim_platform.market.fundamentals` loads and queries the
same data.

`fundamentals_path` may also be an ALFRED all-vintages CSV of a macro series (columns
named `<SERIES>_<YYYYMMDD>` per release; read as `macro:<SERIES>`, each value public from
its vintage date, so revisions stay hidden until released), or a folder holding any mix of
these files. Analyst estimates use the fundamentals CSV format with their own publication
dates.

Attach documents with `documents_path`, a file written by
`sim_platform.market.news_import.convert_documents()` from a CSV or JSON Lines export with
`published_at`, `title` and `text` columns (10-K/10-Q text, transcripts, research notes).
`convert_news()` does the same for a vendor's headline export, producing the news file of
the recording. Both place each item at its publication time, plus an optional
`delay_seconds` for feed latency. To connect a vendor directly, implement the
`DataConnector` protocol in `sim_platform.market.connectors` and call
`export_connector()`: it writes these same files, so the leakage checks still apply.

An optional OS sandbox confines an imported Agent's files and network; see
[the SDK guide](sdk.md). Desktop Agents screens are implemented. Environment preparation is a transient job, not an extra
process in a running experiment; financial and scheduling paths remain the same.

Existing placeholder-only Python definitions are not silently converted: their source
was never saved. Re-import their original source. Historical run data remains on disk.

Try `.venv/bin/python -m sim_platform.examples.agent_project_demo` for a real local,
no-network example of save → environment preparation → reuse, with no Agent/model run.

[Section index](README.md) · [All documentation](../README.md)
