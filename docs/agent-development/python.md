# Choosing Python for an Agent

The App and an Agent can use different Python versions. The project declares its
request in `agent.json`; a new project without this file defaults to CPython 3.12.
Existing saved projects keep their recorded request.

```json
{"python": ">=3.12,<3.14"}
```

A minor such as `3.12` accepts its patch releases; `3.12.13` requires that exact
patch. Comparisons `>=`, `>`, `<=`, `<`, `==` can be combined with commas. The
provisioning policy accepts CPython 3.9–3.14 on the native OS/CPU. This is not a claim
that every dependency or the full product has been certified on every version.
Incompatible dependencies cause a preparation failure; the App does not guess that
code written for a newer Python will work on an older one.

## Preparing an environment

```python
from sim_platform import Platform

with Platform('/absolute/path/app-data') as app:
    owner = app.create_profile('Research')
    saved = app.import_agent(owner, '/absolute/path/my_agent')
    job = app.prepare_agent_environment(
        owner, saved['agent_id'],
        allow_python_download=True,   # missing interpreter
        allow_download=True,          # dependency wheels from PyPI
    )
    try:
        status = job.wait(300)
        print(status['state'], status['phase'])
        if status['state'] == 'failed':
            print(status['error'])
    finally:
        job.close()
```

The two download permissions are independent and default to false. `snapshot()`
returns immediately; `cancel()` requests cancellation. `wait(timeout)` may still
return `preparing`; close or keep the job until finished. Platform owns jobs and
closes them before its stores. Phases include waiting_for_python,
downloading_python/importing_python, checking_python, checking_project and the
existing environment preparation phases. Progress is a phase, not a byte percentage.

Selection first reuses a compatible managed runtime (highest matching installed
version). Repository use can borrow the current App interpreter if compatible;
a frozen App cannot. Otherwise downloads require a trusted uv helper. Configure
its absolute path with `Platform(directory, python_manager='/trusted/path/to/uv')`;
repository use can find uv on PATH. Missing helper produces
PYTHON_MANAGER_UNAVAILABLE, rather than downloading an executable helper implicitly.
The helper installs only under the Platform data directory; it does not install a
system Python or edit PATH/default interpreter registrations.

The complete version request reaches the provider, including narrow patch bounds.
The installed executable is probed for native CPython, exact version and a
self-contained installation. Project syntax is then checked by that interpreter,
without importing or running the project. A private venv and pip resolution use
that same interpreter, so wheel tags, Requires-Python and dependency markers are
resolved for the Agent, not the App. No project build hooks or package imports run.

## Offline preparation

`archive` is an explicitly trusted native standalone CPython `.tar.gz` distribution,
containing `bin/python3`; it is not an Agent project, venv or arbitrary source archive.
Its interpreter will execute during probing and venv creation. Only import a
runtime obtained from a trusted distributor. Extraction rejects traversal, external
links and special files; those checks do not authenticate a distributor.

```python
job = app.prepare_python_runtime('3.12', archive='/offline/cpython.tar.gz')
try:
    result = job.wait(300)
finally:
    job.close()

job = app.prepare_agent_environment(
    owner, saved['agent_id'], wheelhouse='/offline/wheels',
)
```

Alternatively pass `python_archive` directly to prepare_agent_environment(). All
required transitive wheels must be present. An unavailable runtime in offline mode
returns PYTHON_UNAVAILABLE. Download, invalid runtime, version mismatch and invalid
syntax failures are named in the job snapshot. Failed/cancelled candidates are
removed and never published ready; an existing ready installation remains intact.
A process crash can leave an unpublished candidate; crash recovery is not provided.

## Storage and release boundary

`agents/runtimes/<runtime_ref>/runtime.json` records exact version, implementation,
OS, CPU, executable, base_prefix and origin. Environment readiness also records that
runtime descriptor and installed wheel inventory. `python_runtimes()` lists managed
ready records; `agent_environment()` looks up a compatible prepared environment.
These are files, with atomic publication and preparation leases, not SQL receipts
or content hashes. Data directories must remain at their prepared absolute location.

The current verified profile is macOS arm64, App Python 3.9.6, Agent Python 3.12.13
and uv 0.10.8; Agent environment preparation was rechecked with uv 0.13.4, the version
now bundled. Interpreter provisioning supports other declared minors but this
profile is the real cross-version test evidence. Standalone App bundling, signing,
distributor authenticity/update policy and clean-machine acceptance remain the
future work. Saved Agent execution loads through the host once its matching SDK and
dependencies are ready. See [SDK and run guide](sdk.md). Preparation alone does not start it.

Provider reference: [uv Python versions](https://docs.astral.sh/uv/concepts/python-versions/).

[Section index](README.md) · [All documentation](../README.md)
