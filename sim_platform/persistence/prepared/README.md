# Store prepared artifacts

`PreparedStore.begin()` returns an existing artifact path or a caller-owned PreparedImport.
Reuse matches source path/stat, symbol, depth, EOF policy and native library path/stat.
The reader validates the exact artifact layout; this cache does not hash the source.

ImportLease prevents two local writers from preparing the same workspace concurrently.
PreparedImport owns its preparation child and lease. It publishes a complete artifact,
then atomically updates the JSON inventory. Close failed or cancelled imports; do not
release the writer lease until child exit is confirmed.

Each run opens its own read-only mapping. There is no automatic artifact eviction.
Closing one cursor does not delete data used by another. This package owns files, not
financial state or a public setup entry.

Locks use [locking.py](../locking.py): POSIX flock or Windows CRT byte-range locks.
Lock tests alone do not establish full Windows App support.
