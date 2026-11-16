# Native interface map

| Folder | Main interfaces |
| --- | --- |
| [app](app/README.md) | Options and parse_options |
| [book](book/README.md) | Book, BookState and Snapshot |
| [feed](feed/README.md) | File framing, ITCH decoding and MoldUDP64 transport |
| [replay](replay/README.md) | Result, ReplayControl and Checkpoint |
| [output](output/README.md) | CSV formatting and StagedOutput |
| [common](common/README.md) | Binary fields, content digests and file checks |

Headers use namespace `itch`. These folders are not separate CMake library targets.
Some interfaces remain shared: the live receiver returns replay's Result type, and
checkpoint headers expose file checks used by existing callers. A future library split
must address these dependencies, not just move files.

[Implementations](../../src/README.md) · [Coding rules](../../docs/development/coding.md)
