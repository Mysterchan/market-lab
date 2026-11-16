# Market data, time and trading order

Historical data is prepared before an experiment can start. During execution, a shared
clock determines what data may be read, while a single financial owner processes accepted
orders against the ordered market events. These are separate positions.

## Preparation and queries

Preparation decodes and validates the complete source, reconstructs the native book and
publishes immutable query files only after the import audit succeeds. Queries locate a
stored state; they do not rebuild the whole book for every request.

| Prepared file | Purpose |
| --- | --- |
| `manifest.json` | Source identity, symbol/depth, event counts, time coverage and file metadata |
| `events.bin` | Ordered retained events, original source frames and state references |
| `states.bin` | Bounded-depth market states for direct reads |
| `trades.bin` | Trades and the prefixes at which trades/corrections become visible |
| `bar-versions.bin`, `bar-index.bin` | Minute-bar history without exposing later corrections to earlier reads |
| `spans.bin` | Verified stable-book spans that may allow safe financial batching |

A CSV file of price bars is prepared into the same files without the native decoder. Its
manifest also has a `calendar` entry with the first date and the assumptions of its
synthetic book, including the spread and depth chosen when the file was added. Market time counts from that first day's midnight and may span many days.
See [price-bar input](../reference/price-bars.md).

The initial audit validates full contents. Normal reuse checks metadata, file statistics
and boundaries. This is not protection against a malicious replacement with identical
file statistics. The [prepared implementation](../../sim_platform/market/prepared/README.md)
and [store](../../sim_platform/persistence/prepared/README.md) own these formats.

Each market query captures the current clock cutoff. It rejects future or unavailable
coverage and reports actual source time and sequence. News uses availability/revision time;
equal-time news precedes ITCH events and does not increment the financial source sequence.

## Financial commands

The sequencer fixes trusted receipt time and admission order. Agent analysis and transport
time are not backdated to the earlier quote it observed. At equal times, market events are
processed before commands. Processing never uses later data as though it was known at receipt.

Keep these positions distinct: original source frame, retained financial event sequence,
merged boundary including news, and publication ordinal. The financial processed frontier
can lag the visible market cutoff. Prepared queries do not guarantee zero scheduling or
transaction latency.

An accepted unfilled order stays in financial state and may fill partially on later events.
Stable-span batching requires proof that the current run has no executable pending order.
The financial owner validates funds/shares and commits through the shared execution path;
the Tools child formats detached values and cannot decide fills.

## Tool results and Replay

Requests are checked for envelope, tool grants, arguments and work validity. Financial
execution separately checks authority, position, funds and order rules. Responses have named
error codes and retry actions. Market-only results may have no account sequence; account and
financial results identify the actual account prefix. See [tool implementation](../../sim_platform/agents/tools/README.md).

The local JSON log records run setup, command admission/processing/cancellation, tool results
and final state. Replay accepts a complete sealed operation log and executes its recorded
commands without starting the Agent or model. The [experiment flow](experiments.md) explains
this route; [lifecycle](lifecycle.md) defines its completion and shutdown boundaries.

[Section index](README.md) · [All documentation](../README.md)
