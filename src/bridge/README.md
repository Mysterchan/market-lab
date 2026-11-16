# Python market bridge

Each C ABI handle owns a C++ Book and, for local playback, the input file and
one pending payload. Python copies returned JSON strings immediately; strings
remain valid until the next call. Exceptions do not cross the ABI. Failed handles
must be destroyed. The handle has one owner and is not thread-safe.

- `market_create` / `market_destroy`: engine and file resource lifetime.
- `market_open_file`: open a BinaryFILE with the shared BinaryFILE reader contract.
- `market_peek_file`: validate and buffer the next payload, returning only its
  sequence/time. Does not apply to Book. Repeated peeks retain the same event.
- `market_next_file`: apply the pending event and return the market result.
- `market_inspect` / `market_apply`: validation/processing for sequenced fixtures.
- `market_error`: native failure diagnostics.

C++ owns framing, ITCH field decoding and book calculation. Python owns the
simulation clock, publication ordering, subscriptions and point-in-time queries.
The shared `feed/binaryfile/reader` is also used by standalone replay, including strict
terminator checks and implicit EOF only after a complete S/C event.
The bridge adds E/C/P/Q trade metadata; it does not calculate another order book.

Build with `make all` or CMake `itch_market`. See
[Experiment architecture](../../docs/architecture/experiments.md).

## Batched prepared-market import

`engine.hpp/.cpp` owns the typed apply result and shared symbol/trade/publication
validation used by both the existing JSON C ABI and the prepared exporter. The
original decoder and full Book remain the only reconstruction implementation.
`prepared.hpp/.cpp` exposes a single-owner export handle: create, next (at most
8192 source frames), error, destroy. The exporter validates the complete source,
checks monotonic session timestamps and writes selected-symbol/global S events
and deduplicated bounded-depth states directly as big-endian binary. Original
source frames are retained separately from contiguous prepared ordinals.

Python owns staging, cancellation between batches, progress, source mutation
checks, fsync, full structural validation and final publication. No per-frame
JSON or Python call is required; next returns one small JSON summary per batch.
Optional per-frame native stage timers are diagnostic and disabled by default.
No prepared handle, path or full future file is an Agent capability. See the
[Experiment architecture](../../docs/architecture/experiments.md) and
[Verification and support scope](../../docs/development/verification.md).
