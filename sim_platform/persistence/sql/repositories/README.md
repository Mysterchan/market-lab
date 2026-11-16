# Repositories by stored object

`__init__.py` assembles Repositories and explicitly exports its classes.
DatabaseOperations owns transaction creation and commit.

| File | Stored values |
| --- | --- |
| `base.py` | Shared row cache, lookup and domain conversion |
| `profiles.py`, `accounts.py`, `makers.py` | Identity, ownership and lists |
| `runs.py` | Run settings/state, audit, archive and execution checkpoints |
| `events.py` | Ledger events, pagination and streaming |
| `checkpoints.py` | Account checkpoints and intent/order projections |
| `agents.py` | Immutable Agent revisions and prepared experiments |

Change a query in its owning repository. Add an assembly entry only for a new repository.
Return domain values or detached JSON, never ORM rows to a worker or UI. Keep related
operations in the caller's single Session and transaction.
