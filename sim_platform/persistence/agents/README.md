# Agent project and environment files

`projects.py` captures bounded UTF-8 source trees, excludes credential/generated
files, rejects symlinks/special files and publishes copied project snapshots. POSIX uses
directory descriptors; `project_capture_windows.py` uses path/handle observations and
rejects reparse points without POSIX-only open flags. Concurrent hostile writers are not
an isolation guarantee.
Snapshots are API-immutable: importing edits creates a new reference and revision.
Saved and per-execution copies preserve the captured UTF-8 bytes, including original line
endings; writing them must not apply OS newline conversion.
No API modifies saved source in place. Direct edits to private storage are unsupported;
there is no added content-hash or authenticated tamper-proof storage claim.

`lease.py` owns cancellable preparation leases backed by the shared OS-specific file locks. `runtimes.py` owns private runtime
records and confined offline extraction. `environments.py` owns unique environment
directories, exact-specification lookup and atomic readiness records. A ready marker
is published only after successful installation. Failed/cancelled candidates are
removed; previously ready environments remain. There is no crash-resume feature.

Under the Platform data directory:

```text
agents/
  projects/<project_ref>/
    project.json                 Inventory, entrypoint, Python, requirements, owner
    source/agent.py              Captured source, never loaded in the App
    source/...                   Helpers and permitted text assets
  runtimes/<runtime_ref>/
    installation/                Native standalone CPython
    runtime.json                 Atomic ready record and exact interpreter identity
  environments/
    .prepare.lock                Cross-instance preparation lease
    <environment_ref>/
      python/                    Managed venv at its permanent location
      wheels/                    Resolved wheel artifacts
      environment.json           Atomic ready record and package inventory
```

Agent names, profiles and revisions stay in the existing SQL agent_revisions table.
Definitions contain package.project_ref only; the platform creates that reference.
Environment records are files, not a new SQL table or tool/transaction receipt.
The original project and .env remain outside this managed snapshot.

`sdk.py` builds/exports the client-only pure-Python wheel with standard wheel metadata
and atomic cache publication. Its inventory/identity are in sdk/distribution.py.
Per-experiment writable source copies are App-owned workspace files, implemented in
persistence/workspace/agent_source.py; hosts close them after child/IPC cleanup.
