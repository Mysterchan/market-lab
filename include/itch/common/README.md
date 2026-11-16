# Shared native primitives

- [binary.hpp](binary.hpp): big-endian field reads/writes. Width is checked; callers
  must also validate the buffer bounds.
- [digest.hpp](digest.hpp): SHA-256 content identity and FNV consistency digests.
  Neither is a signature or source authentication.
- [file_utils.hpp](file_utils.hpp): reject paths that refer to the same file;
  filesystem errors can propagate. This is not a cross-process lock.

These helpers depend on the standard library, not book or transport policy.
Keep single-file helpers with their caller.

[Implementations](../../../src/common/README.md)
