# File replay and checkpoint recovery

[replay.cpp](replay.cpp) reads complete BinaryFILE frames in order, drives the shared
book, emits selected events and invokes checkpoint callbacks at valid boundaries.
[checkpoint.cpp](checkpoint.cpp) encodes/loads state, checks input identity and frame
boundaries, saves atomically and verifies/copies the recorded CSV prefix.

Input is uncompressed. The default requires a zero-length end marker; compatibility
options have separate rules in the [input guide](../../docs/reference/itch-input.md).
Checkpoints bind the full input SHA-256 and cannot resume a network session.
The caller owns output publication because replay errors can leave a partial prefix.

Run `make test` to check prefix/suffix equivalence, corruption, wrong source/boundary,
uncommitted journal tails and interrupted recovery. Full-day validation needs separately
supplied permitted data; see [verification](../../docs/development/verification.md).

[Headers](../../include/itch/replay/README.md) · [Native map](../README.md)
