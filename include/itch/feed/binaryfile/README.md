# BinaryFILE interface

`reader.hpp` declares `read_binary_frame`, shared by standalone replay and the Python bridge.
Returns one full payload, or nullopt for a valid end; throws for broken framing.
The caller owns the stream, frame/byte-offset counters and last-S/C state.
Checkpoint callers must validate the input identity and boundary before seeking.

[Implementation](../../../../src/feed/binaryfile/README.md)
