# Preparation lifecycle

Shared infrastructure for interpreter and dependency preparation, outside the live
experiment pipeline. `job.py` owns the background worker, progress snapshots,
cancellation and close. `process.py` runs bounded, cancellable trusted subprocesses
with a reduced environment. POSIX commands use their own process group; Windows commands
are attached to the existing Job Object owner and release that job during cleanup. `errors.py` defines named preparation failures.

Runtime selection belongs in `agents/runtimes`; package resolution belongs in
`agents/environments`; filesystem locking and publication belong in persistence.
