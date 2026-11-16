# Historical market input and queries

This package reads market data. It does not accept user orders, own accounts or open a
financial database. Normal public entrypoints are supplied by Platform.

| Location | Responsibility |
| --- | --- |
| `prepared/` | Whole-source preparation/reuse, immutable indexed states/history, clock-limited views and research cursor |
| `native.py`, `feed.py` | C++ bridge and BinaryFILE framing/metadata |
| `session.py`, `playback.py` | Standalone native market state and controlled playback/query capabilities |
| `models.py` | Market snapshots, events and publication values |
| `history.py`, `window.py` | Standalone history/cache and bounded detached history captures |
| `pacing.py` | Caller-driven playback pacing; not a separate clock thread |

Research experiments use the prepared source path. The original native order book is rebuilt
during preparation, not for every runtime query. A fixed cutoff returns data visible at that
time; it does not mean the financial event frontier has already caught up.

`Platform.open_market()` separately supports standalone native market reading/playback.
Its cache/history costs differ from prepared indexed queries. Do not use a standalone
history benchmark as evidence for the complete Agent Tools IPC path.

See [prepared implementation](prepared/README.md), [data/tool contract](../../docs/architecture/market-data.md)
and [native engine](../../docs/architecture/native-engine.md).
