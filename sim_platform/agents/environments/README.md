# Dependency environment preparation

This package prepares dependencies; it does not run an Agent or join its live pipeline.

- `preparation.py`: EnvironmentPreparation owns a background job, cancellation,
  detached progress snapshots and cleanup. Platform owns the returned job.
- `installer.py`: wheel-only resolution, static wheel inspection and bounded transient
  Python/pip subprocesses. It cancels the subprocess group and bounds output/time.

Project inspection lives in agents/projects. The filesystem cache, readiness records
and cross-process preparation lease live in persistence/agents/environments.py;
Platform injects that capability. No database transaction stays open while installing.

A successful environment is reused by its dependency specification and interpreter
identity, not by Agent source. Each environment has an opaque generated reference;
there is no project/account/experiment hash. No installed package is imported to test
readiness, and no readiness probe starts the interpreter after the final installation.

Runtime selection is in agents/runtimes; the venv and wheel resolver use that selected
interpreter independently of App Python. The shared `environment_python` rule selects POSIX `bin/python` or Windows `Scripts/python.exe`
for installation and saved-environment lookup. Real preparation acceptance remains macOS/POSIX;
the Windows-layout regression is not whole-App Windows acceptance.
Only compatible prebuilt wheels are supported. No source builds, editable installs,
project build hooks, direct dependency URLs or Python startup hooks are accepted.
This is preparation hygiene, not OS isolation for executing arbitrary Agent programs.

See [usage, states and errors](../../../docs/agent-development/projects.md).

Preparation also installs the matching local SDK wheel without network/dependency
resolution. The environment specification records its normal distribution identity.
User dependencies remain in packages; SDK identity is a separate specification field.
