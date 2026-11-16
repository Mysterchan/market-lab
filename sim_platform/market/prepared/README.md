# Prepared historical data

Preparation validates the entire source before an experiment can start. The C++ core
rebuilds the book once and writes event/state batches. Only the selected symbol and
global system events are retained. A successful build publishes the completed directory.

| File | Responsibility |
| --- | --- |
| `builder.py`, `native.py` | Drive the native exporter; progress, cancellation and batch summaries |
| `job.py` | Own the short-lived preparation process and bounded shutdown |
| `codec.py` | Exact binary layouts for events, trades and state versions |
| `reader.py` | Map immutable files and query them by time; VisibleMarket enforces the clock cutoff |
| `history.py` | Trade corrections and minute-bar versions without replaying the book |
| `publications.py` | Merge trusted news by availability/revision; keep financial source ordinals separate |
| `session.py` | Cancellable preparation, independent cursors and start-position selection |
| `bars.py` | CSV price bars: reading, the synthetic book and its spread/depth/first-date options |
| `merged.py` | Several symbols' sessions played as one timeline (time, then universe order) for one experiment |
| `disguise.py` | A disguised copy of a price-bar file: dates moved by whole weeks, prices scaled ([evaluation](../../../docs/user-guide/evaluation.md)) |
| `spans.py` | Describe stable book spans; execution must also prove that orders cannot fill |

Opening a reader checks metadata and boundaries. `verify=True` or `validate()` performs
a full artifact audit. Queries use indexed state versions rather than rebuilding the book.
Prepared ordinals and original source frame numbers are different positions.

There is one current artifact layout. Invalid or obsolete artifacts must be prepared
again. The package has no SQL, execution or UI dependencies. Never pass its reader or
mapped files to user Agent code.

[Data contract](../../../docs/architecture/market-data.md) ·
[Tests](../../../tests/platform/test_prepared_market.py) ·
[Preparation benchmark](../../../tools/benchmark_prepared_import.py)
