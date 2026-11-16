# Design proposal: one experiment, several symbols

Status: **accepted** on 2026-11-14 with the proposed answers to the five decisions below.
Implemented: Controlled manual, rule, hold and model experiments on several symbols; Live
manual and Agent experiments on several symbols (a `MergedTimelineReader` gives Live and
Agent scheduling the merged market's stepping order); their Replay; and the Desktop setup,
workspace and result page for manual and model runs. The user guide is
[Trade several symbols](../user-guide/several-symbols.md). News merged across symbols
follows later.

## Goal

One experiment trades several symbols from one account: one cash balance, one position
per symbol, one shared clock and one ordered timeline. A strategy sees every symbol and can
trade any of them. Existing single-symbol runs keep their results and still pass Replay.

## Where the code stands

Some of this already exists. A run stores a `universe` list, the ledger keeps positions
keyed by symbol, reservations already share one cash balance and keep shares per symbol,
and Agent tools take a `symbol` argument. Everything else assumes a single symbol. The
main single-symbol assumptions are:

- the prepared session reads one symbol's book;
- the execution frontier holds one book's supply budget;
- `run.universe` must equal `[market.symbol]`;
- an observation has one `market`;
- Replay records one `source`.

## 1. Market and timeline (needed by all four)

A new `MergedMarket` composes one `PreparedSession` per symbol: one ITCH file prepared once
per symbol, or one CSV per symbol. It replays their events as a single ordered timeline:

- **Order**: by market time. Events at the same nanosecond go in universe order, then by
  each source's own order. This tie-break is new and is written into the manifest.
- **Sequence**: a merged market sequence numbers every event. Each event also keeps its
  symbol and its per-source sequence.
- **Calendars**: CSV recordings must share a first date, or be re-based to the earliest
  one. Their closed-market gaps are merged so the clock only skips when every symbol is
  closed.

## 2. Account and ledger

**No ledger change is needed**: cash, fees and realized P&L are account totals, and
positions are already per symbol.

- **Valuation and statistics**: portfolio value marks each position against its own book.
  The result page computes round trips, win rate and holding time per symbol, then adds
  them up. Turnover and Sharpe use the whole account.
- **Per-symbol realized P&L**: computed in the presentation layer from fills, with the same
  average-cost rule. The ledger keeps one total, so existing ledgers and checkpoints are
  unchanged.

## 3. Risk

- **Symbol check**: `rejection_reason` checks the intent's symbol against `run.universe`
  instead of `market.symbol`, and runs `market_reason` on that symbol's own snapshot. A
  halt on one symbol does not block the others.
- **Reservations**: cash stays shared and shares stay per symbol. Short margin and the
  short hold apply per symbol and add up.
- **Possible new limit (needs a decision)**: a cap on gross exposure across symbols, for
  example gross value ≤ N × equity.

## 4. Execution

- **Frontier**: becomes `books: {symbol: {market, bids, asks}}` plus the merged
  `market_sequence`. A one-symbol run keeps today's flat shape, `market`/`bids`/`asks`,
  and the engine chooses the path by which shape it finds.
- **Matching**: `advance` matches only orders of the event's symbol, against that
  symbol's budget. Fills otherwise follow today's rules.
- **Order eligibility**: an order still needs a later event than the one it was accepted
  on, compared on the merged sequence. Latency is unchanged.
- **Checks to relax**: `bind` and `ExecutionSession` stop requiring
  `universe == [market.symbol]`, and require the universe to equal the merged market's
  symbols instead.

## 5. Replay

- **Manifest**: the replay manifest gains `sources`, one entry per symbol in today's
  `kind: prepared` form, plus `merge` with the universe order and tie-break rule. A
  single-symbol run keeps today's `source` key, and Replay reads either form.
- **Process**: Replay rebuilds the merged market from the recorded sources, checks each
  prepared shape, seeks every symbol to the recorded origin and applies recorded decisions.
- **Comparison**: the recorded financial events already carry `market_sequence`; for
  merged runs that is the merged sequence.
- **Observation fingerprints**: an observation gains an optional `markets` mapping,
  present only for multi-symbol runs, as `bars` is today. Single-symbol fingerprints do
  not change, so existing runs still verify.

## Methods and UI

- **Policies**: `validate_response` accepts any universe symbol.
- **Model**: `decide()` receives bars and quotes per symbol and may return a target per
  symbol: `{"targets": {"AAPL": 100, "MSFT": -20}}`.
- **Agent tools**: check `symbol ∈ universe`, and `environment.describe` lists the
  universe.
- **Desktop**: the setup page selects several recordings or symbols. The workspace gets a
  symbol switcher for the chart, depth and ticket, with positions listed per symbol.
- **Proposed order of work**:
  1. timeline and execution, with manual and rule methods in Controlled mode;
  2. model and Replay;
  3. Agent and Live.

## How existing single-symbol runs stay valid

- With a one-symbol universe, every new field is absent: no `books`, `markets`, `sources`
  or `merge`. Code takes today's path.
- A regression test runs today's single-day ITCH fixtures before and after the change and
  compares fills, ledger, journal and Replay byte for byte. The test is required whenever
  execution, market or scheduling code changes.
- Runs saved before the change are replayed in that test from stored fixtures, not only
  from fresh runs.

## Decisions (accepted)

1. Events at the same nanosecond are processed in universe order, the order the symbols
   were chosen in, then in each source's own order.
2. CSV recordings with different first dates are re-based to the earliest first date, which
   becomes market day 0 for every symbol.
3. No gross-exposure limit: buying power stays today's cash rule, with short margin per
   symbol added up.
4. The first delivery covers Controlled manual, rule and model experiments and Replay.
   Agent and Live follow later. (Since delivered; see the status above.)
5. Fees and latency stay one setting per run.

[Section index](README.md) · [All documentation](../README.md)
