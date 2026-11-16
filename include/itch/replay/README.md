# File replay and checkpoints

[replay.hpp](replay.hpp) declares Result, ReplayControl and replay(): ordered input,
optional stopping and checkpoint callbacks. [checkpoint.hpp](checkpoint.hpp) declares
checkpoint save/load and source-boundary/CSV-prefix checks, including supported v1/v2 files.

Replay reads uncompressed BinaryFILE. The default requires a zero-length end marker;
source compatibility options have explicit rules. Checkpoints bind the full source
SHA-256 and valid frame boundary. They cannot resume a network session.
Errors may leave an output prefix; callers should use staged output for a final file.

[Implementation](../../../src/replay/README.md) · [Input rules](../../../docs/reference/itch-input.md)
