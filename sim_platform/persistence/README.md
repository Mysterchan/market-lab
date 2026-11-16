# Local persistence

Persistence owns SQL/file operations, not trading decisions or experiment scheduling.
Platform injects these capabilities and controls their lifetime. ORM sessions stay within
short units of work; detached values cross into callers.

| Location | Responsibility |
| --- | --- |
| `sql/database.py`, `sql/models.py` | Database/session lifetime and ORM entities |
| `sql/repositories/`, `sql/migrations/` | Queries, atomic writes and preserved migration history |
| `agents/` | Private source snapshots, Python/dependency environments, readiness/lease records and SDK artifacts |
| `prepared/` | Prepared artifact inventory and publication |
| `datasets/` | Saved local source references and managed copies |
| `workspace/` | App temporary workspaces, source cache files and cleanup |
| `replay/` | Isolated recorded-run inputs/destination handling |
| `agent_log_files.py` | Ordered JSON activity files later archived into the existing run record |
| `locking.py` | Shared POSIX/Windows exclusive file locks; callers own streams and lock lifetime |
| `paths.py` | OS-specific application data location |

Financial events/projections/checkpoints are saved through the existing transaction path.
Tool/control records use ordinary JSON during execution and are archived at terminal output;
there is no additional tool-receipt SQL table. Preserve migration identifiers and historical
rows when changing code. Decision order references reconstruct their original ledger prefix;
current projection rows cannot substitute for past observations.

Temporary workspaces are separate from persistent account storage. Filesystem locks and
cleanup have platform-specific limits; do not infer full Windows App support from Agent
host CI. Reset/backup semantics belong to Platform lifecycle, not an arbitrary repository.

See [current support](../../docs/user-guide/support.md), [architecture](../../docs/architecture/experiments.md)
and [tests](../../docs/development/verification.md).
