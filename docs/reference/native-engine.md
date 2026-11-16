# Standalone native replay

`build/itch_replay` reconstructs the public market book from ITCH BinaryFILE and writes
snapshots. It does not own a simulated account or place a user's trading orders.
For the Python application, start with the [user guide](../user-guide/README.md).

## Build and replay

```bash
make all
build/itch_replay --input /absolute/path/feed.bin --symbol AAPL \
  --depth 5 --with-offset --output /absolute/path/aapl.csv
```

The input is uncompressed. Decompress gzip separately and use data you have permission to
process. The executable does not implement a successful `--help` command; use this reference.
A C++17 compiler is required. The build also produces the Python native market library.

| Flag | Value / effect |
| --- | --- |
| `--input FILE` | Required uncompressed ITCH BinaryFILE |
| `--symbol SYMBOL` | Required, 1–8 bytes; case is not normalized |
| `--output CSV` | Publish a staged file on success; otherwise CSV goes directly to stdout |
| `--depth N` | Display levels per side; default 5, range 1–1000; internal orders are not depth-limited |
| `--with-offset` | Include offsets in the uncompressed source |
| `--allow-implicit-eof` | Accept only immediate EOF after a complete locate-zero End-of-Messages event |
| `--check-book` | Recompute price-level aggregation for extra book checks; potentially expensive |
| `--verify` | Repeat replay from the same starting point and compare results; not an independent correctness proof |
| `--checkpoint-out FILE` | Save a checkpoint at successful termination, plus requested periodic saves |
| `--checkpoint-every-frames N` | Save at positive absolute frame multiples; requires a save destination |
| `--stop-after-frame N` | Save and stop after absolute frame N; 0 means before processing any event |
| `--resume FILE` | Load saved state and resume at the next frame; does not by itself request saving |
| `--resume-csv` | Continue the verified saved CSV prefix; requires resume, output and a CSV-bound checkpoint/journal |

Frame counts refer to BinaryFILE records, not network sequence numbers. Parent output
directories must exist. Input, output and checkpoint paths must not alias protected files;
loading and saving the same checkpoint is allowed. Resume must retain compatible symbol,
depth, offset and ending options. Unsupported flags or missing/invalid values fail.
See the [input contract](itch-input.md) for accepted messages and truncation behavior.

## Saving replay state and continuing CSV output

Three choices are separate: where computation begins, which CSV segment is published, and
whether a checkpoint is written. Plain `--resume` emits only the new suffix. `--resume-csv`
verifies and copies the saved journal prefix, then appends new rows into a new journal.
It does not itself enable checkpoint writes.

| Save choice | Destination |
| --- | --- |
| Explicit `--checkpoint-out` | That path |
| Resume plus periodic save or explicit stop, without checkpoint-out | Update the loaded checkpoint |
| Resume alone, including resume-csv alone | No checkpoint update |
| First run with periodic save/stop but no checkpoint-out | Rejected |

Examples below are alternatives; keep the original checkpoint/journal if testing multiple
recovery modes.

```bash
# Replay to frame 5, retaining recoverable state and CSV prefix.
build/itch_replay --input feed.bin --symbol AAPL --output aapl.csv \
  --checkpoint-out aapl.cp --checkpoint-every-frames 2 --stop-after-frame 5

# Resume computation, publishing only the suffix. The original checkpoint is unchanged.
build/itch_replay --input feed.bin --symbol AAPL --resume aapl.cp --output suffix.csv

# Continue complete CSV and keep updating the checkpoint/journal binding.
build/itch_replay --input feed.bin --symbol AAPL --resume aapl.cp --resume-csv \
  --output aapl.csv --checkpoint-every-frames 2
```

A checkpoint is a saved replay state used to resume processing without starting again.
It records source identity/boundary, book state, compatible options and accumulated
statistics. A CSV-bound checkpoint additionally records a trusted journal prefix. Preserve
the checkpoint and its referenced journal together; journal references use absolute paths.
A state-only checkpoint supports computation/suffix recovery, not complete CSV continuation.
Plain resume followed by another save produces state-only recovery, so keep using
`--resume-csv` if you need a checkpoint bound to complete CSV output.

If the journal contains rows beyond the saved boundary, recovery ignores that tail and
reprocesses source from the checkpoint. The copied prefix must pass size/header/row-count
and integrity checks. A missing, truncated or inconsistent prefix is rejected. Extra rows
are not treated as committed progress. Do not indiscriminately delete recovery `.partial.*`
files: an existing checkpoint may reference one.

## Publication, errors and statistics

With `--output`, replay and optional verification complete before staged CSV publication.
An ordinary failure preserves an existing output and the most recent successful checkpoint.
A stopped prefix has not validated the unread remainder or ending. stdout may already
contain partial output on failure; always inspect exit status. Checkpoint/CSV publication is
not a multi-file database transaction or a general power-loss durability guarantee.

| stderr statistic | Meaning |
| --- | --- |
| `frames`, `selected`, `hash` | Cumulative frames, data rows and row hash, including loaded progress |
| `emitted` | Newly computed rows in this invocation |
| `scope=full/prefix/suffix` | Published coverage, separate from whether input ending was reached |
| `complete=yes/no` | Valid ending reached, or stopped before it |
| `verified=yes` | Requested repeatability pass completed |
| `elapsed_ms`, `frames_per_s` | Replay work, including scans/checkpoints; not total process/publication time |
| `mean_selected_event_ns`, `max_selected_event_ns` | Selected native book/snapshot/formatting work; not end-to-end network latency |
| `peak_rss_bytes` | Whole-process peak memory |

`--verify` does not run a second replay at every periodic checkpoint. A checkpoint may be
recoverable before final repeatability verification has completed.

Implementation: [options](../../src/app/options.cpp), [checkpoint serialization](../../src/replay/checkpoint.cpp),
[staged publication](../../src/output/staged_output.cpp). Tests: [replay regression](../../tests/test_replay.py)
and opt-in [full-day recovery check](../../tests/verify_checkpoint_day.py).
For loopback UDP, see [network input](network-input.md); for component ownership, see
[native architecture](../architecture/native-engine.md). No real exchange connection is claimed.

[Section index](README.md) · [All documentation](../README.md)
