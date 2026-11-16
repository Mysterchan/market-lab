# Native implementation map

The native build produces `itch_replay` and the `itch_market` shared library used by
Python. Interfaces live under [include/itch/](../include/itch/README.md).

| Folder | Responsibility |
| --- | --- |
| [app](app/README.md) | CLI parsing and output/checkpoint coordination |
| [book](book/README.md) | Visible orders, price levels and state checks |
| [feed](feed/README.md) | File framing, ITCH decoding and network transport |
| [replay](replay/README.md) | Ordered file playback and checkpoint recovery |
| [output](output/README.md) | CSV and staged file publication |
| [common](common/README.md) | Shared binary, digest and path primitives |
| [bridge](bridge/README.md) | Native market/preparation handles exposed to Python |

File flow: BinaryFILE reader -> ITCH decoder -> Book -> CSV or Python bridge.
Network flow: MoldUDP64 receiver -> sequence/recovery checks -> the same decoder and Book.
The App coordinates final output publication.

Run `make test` from the repository root. Networking tests require local UDP sockets.
New or moved C++ files must be listed in both Makefile and CMakeLists.txt; neither build
silently includes every source file. Keep single-file helpers private. See the
[coding rules](../docs/development/coding.md) and [test guide](../tests/README.md).
