# Shared binary, digest and path primitives

- [binary.cpp](binary.cpp): read/write/append unsigned big-endian fields of 1-8 bytes.
- [digest.cpp](digest.cpp): memory/file SHA-256 and shared FNV byte/CSV consistency digests.
- [file_utils.cpp](file_utils.cpp): reject aliases and conflicting canonical paths;
  empty optional paths are ignored.

Callers must validate buffer lengths. Hashes check identity or consistency, not source
signatures. File checks do not lock paths. Keep transport, book and replay policy out
of this folder; add a helper only when several callers share the same responsibility.

Run `make test` for checkpoint identity, CSV-prefix, path and network consumers.

[Headers](../../include/itch/common/README.md) · [Native map](../README.md)
