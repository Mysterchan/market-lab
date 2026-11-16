# Workspace lifetime and ownership

`temporary.py` allocates AppWorkspace, detects live owners and removes abandoned temporary
data. `market_cache.py` stores non-SQL query cache files; `ownership.py` owns the account's
cross-instance execution lease; `reset.py` handles storage replacement and rollback.

Platform/lifecycle creates and closes these resources. Shared file-lock primitives live in
[locking.py](../locking.py): POSIX uses flock, Windows uses an exclusive byte-range CRT lock.
Maintenance, owner, account and artifact locks retain separate purposes and lifetimes.
Workspace cleanup releases file handles before deleting their paths while still holding
maintenance ownership. Platform must close child users before workspace removal.

These portable primitives do not by themselves establish full Windows App acceptance.
See [verification](../../../docs/development/verification.md) and
[cross-platform lock tests](../../../tests/platform/test_file_locks.py).
