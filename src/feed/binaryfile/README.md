# BinaryFILE reader

`reader.cpp` extracts the existing v1 framing loop into `read_binary_frame`.
Both standalone replay and incremental file playback use this implementation.
Reads two-byte big-endian length then exactly that many payload bytes; zero length
ends the session, with no trailing bytes allowed. Implicit EOF requires an explicit
option and the last complete message to be stock locate 0 S/C.

The caller owns the stream and counters. Validated v1 checkpoints can supply the
resume frame/offset/end-message state. The reader never seeks arbitrary boundaries
or guesses recovery; the existing ITCH decoder validates payload type/length.
Bridge prefetch validates but does not apply the buffered payload to Book.
