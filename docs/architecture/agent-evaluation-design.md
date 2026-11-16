# Design proposal: evaluating Agents against baselines

Status: **accepted** on 2026-11-15. The project owner delegated the decisions below; each
one follows common quant-research practice and can be revisited. All phases are
implemented ([user guide](../user-guide/evaluation.md)), including turn-based Agents
(decision 7).

## Goal

A quantitative researcher asks one question: *does this Agent trade better than a person,
a rule or a model on the same market, and is the difference real?* Market Lab already
runs all four on the same execution path. What it lacks is a way to put their results side
by side, to repeat a stochastic Agent enough times to measure it, and to rule out the
most common objection to LLM trading results: the model may remember the prices.

## Where the code stands

- One run produces a saved report: run configuration, fills, archived bars and daily
  closes. The Desktop result page computes Sharpe ratio, maximum drawdown, win rate,
  turnover and holding time from it, in `desktop/presentation.py`. Python and batch
  callers cannot reach those numbers.
- `ResearchBatch.compare()` runs up to 20 Rule or model-file configurations once each on
  independent accounts and lists their summaries. It has no repetitions, statistics or
  Agent entries.
- An Agent run calls a model whose replies differ between runs. One run of an Agent is one
  sample, not its performance.
- A price-bar file keeps its real ticker, dates and prices. A model trained after those
  dates may have seen them.

## 1. Metrics move out of the Desktop

`research/metrics.py` holds the per-run metrics as pure functions of a saved run's view.
The Desktop result page, the batch API and the evaluation report all call it, so each figure
has one definition. New figures:

| Metric | Definition |
| --- | --- |
| Total return | final equity / starting cash − 1, equity marked at the last archived close |
| Maximum drawdown | already computed; now also returned to Python |
| Sortino ratio | mean daily return / downside deviation (returns below 0), × √252 |
| Exposure | share of archived bars that end with a position open |

Each stays `None` when it cannot be computed, as today. Risk-free rate is zero.

## 2. An evaluation is built from saved runs

**Decision 1: an evaluation report reads saved runs; it does not own how they were run.**
`Platform.evaluation(profile, entries, baseline=...)` takes named groups of run ids, for
example `{"Agent v3": [...], "Buy and hold": [...], "LSTM": [...]}`. Runs made in the
Desktop, from the batch API or by a script all count the same. The batch runner in section
3 is one way to produce those runs, not the only one.

**Decision 2: runs must be comparable or the report refuses them.** All runs in one
evaluation must share the market identity (recording file size and modification time,
news, synthetic-book options, symbols and prepared start), starting cash, the execution
settings and the short-selling rule. A mismatch raises an error naming the differing field.
Equal footing is the point of the comparison. Live or Controlled pacing may differ. An
Agent stops at its chosen end while other methods run to the end of the recording; the
report warns when the windows differ rather than refusing.

## 3. Repetitions and statistics

**Decision 3: repeat stochastic entries; run deterministic ones once.**
`ResearchBatch.evaluate_suite(paths, entries)` runs each entry `repetitions` times on each
recording (default 10 for an Agent, once for a rule or model file), each run on its own
new account, optionally in the researcher's profile so the runs can be evaluated with
saved Agent runs. A baseline whose values are all identical is treated as a constant.

**Decision 3a: Agents keep real time; concurrency only shortens the wait.** An Agent's
reaction time is part of what Live measures, so a suite never accelerates the market for
it. Rule and model-file runs in the same suite start at the Agents' `start_ns` and stop at
the first event at or after their `end_ns`, so every run covers the same market time.
`concurrency` (1–16, default 1) plays several Agent runs at once, each on its own account
and clock; markets are prepared one at a time first, since a workspace prepares one market
at once. The researcher chooses the level, because a model provider that slows down under
concurrent requests would change every run's results. Turn-based Agents (decision 7)
answer the other question, decision quality without speed.

Repetitions on one recording measure the Agent's own randomness on that market, not
market uncertainty. A suite therefore accepts several recordings and reports each one
separately; robustness across periods is read across recordings, not inferred from
repetitions.

**Decision 4: statistics without assumptions about the distribution.** For each entry and
metric the report gives n, mean, sample standard deviation, min, max and a 95% percentile
bootstrap confidence interval of the mean (10,000 resamples). Against the baseline entry
it gives the difference in means, its bootstrap interval and a two-sided permutation
p-value (exact when there are few enough arrangements, else 10,000 permutations). When the
baseline is deterministic, the test is a sign-flip test of the entry's values minus the
baseline's single value.

- p-values across entries are adjusted with **Holm's method**, because comparing several
  Agents with one baseline is several tests.
- The random generator is seeded (default 2026) and the seed is written into the report,
  so the same runs give the same report.
- With fewer than 5 values in an entry, its interval and p-value are reported but marked
  `insufficient`; the Desktop shows them greyed.
- No overall score is invented. The researcher chooses the metric; total return is the
  default.

## 4. Disguised market data

**Decision 5: disguise the data, not the Agent's view of it.**
`Platform.disguise_recording(path)` writes a disguised copy of a price-bar CSV under a
neutral file name, and every method then uses that copy as an ordinary recording:

- **Symbol**: a CSV carries no ticker; the researcher gives the experiment a neutral
  symbol such as `ASSET1`.
- **Dates**: shifted by a whole number of weeks so weekdays are kept. Default first date
  2100-01-04, a Monday, which no model has seen.
- **Prices**: multiplied by one factor and rounded to the cent; volumes unchanged. Default
  factor drawn from [0.5, 2] with a seed. Returns are kept up to rounding.
- **Other columns** such as `Adj Close` are dropped.

Because the copy is the recording, every participant sees the same disguised market,
Replay needs nothing new, and the comparison stays fair. The mapping is returned to the
researcher only. News and ITCH recordings cannot be disguised in this phase.

**Decision 6: record what the model might know, and warn; never block.** The evaluation
takes a training-data cutoff date per entry, since the researcher knows which model each
entry used. A run whose data dates lie in the future is recognised as disguised without any
extra record. The evaluation report flags each Agent run:

| Flag | Meaning |
| --- | --- |
| `disguised` | data was disguised |
| `after-cutoff` | every data date is after the declared cutoff |
| `possible-memorisation` | real dates on or before the cutoff |
| `cutoff-unknown` | real dates and no declared cutoff |
| `dates-unknown` | an ITCH recording, which carries no date |

## 5. Model cost and latency

The Agent keeps only the token counts of each provider reply (`input_tokens`,
`output_tokens`, `total_tokens`, cached input tokens) and sends them with the model
receipt; the Runtime checks they are non-negative integers and writes them into the
`attempt_completed` record of the operation log. Each `attempt_dispatched` record now
carries its dispatch time. The evaluation reads the sealed log of each Agent run and adds
model calls, failed calls, input and output tokens and mean reply latency. Money cost is
tokens × a price the researcher enters; Market Lab ships no price list, because provider
prices change.

## 6. Desktop Compare runs page

**Compare runs** lists finished runs grouped by what made their decisions: an Agent
revision, a rule with its settings, a model file or manual trading. Once one run is chosen,
only runs with the same market and settings stay selectable, so the comparability check
of section 2 rarely has to refuse. The researcher picks the metric, the baseline and, for
Agent groups, the model's training cutoff; the page shows the table from section 3 with
the flags from section 4 and exports the report as JSON. When neither the baseline nor an
entry varies between runs (rules repeat exactly), the difference is exact and the page
says so instead of showing a test that could never reach significance.

## 7. Turn-based Agents

**Decision 7: a turn-based Agent is the screening mode; Live is the validation mode.**
Agent settings take `speed='turn'` beside `'1x'`. The experiment clock is then turn-based:
it never moves with real time, and a `TurnPacer` thread in the owning process advances it.
The pacer moves the clock only when the experiment is settled at the current time: every
market event up to it processed, every market notification received by the Runtime, the
Runtime fully stepped at that time (it reports `stepped_ns` and `next_ns` only in this
mode), no Agent work running and no model reply outstanding. It then jumps to the earliest
of the Runtime's next own time, the next market event and the end. The owner executes
events and commands stamped before its clock, so an event or a command at the current
instant gets one nanosecond. A model reply outstanding longer than the attempt timeout in
real time is given its deadline, so the attempt times out as it would in Live.

Evaluations refuse to mix Live and turn-based Agent runs, report no reply time for
turn-based runs, and `latency_cost()` reports the Live mean minus the turn-based mean of
the same Agent on the same market with a bootstrap interval.

## Phases

| Phase | Scope | Status |
| --- | --- | --- |
| 1 | Metrics module; Desktop uses it | Implemented |
| 2 | Evaluation report from saved runs, statistics, comparability check | Implemented |
| 3 | `evaluate_suite` for Rule and model-file entries, several recordings | Implemented |
| 4 | Disguised price-bar recordings; memorisation flags | Implemented |
| 5a | Agent entries in `evaluate_suite`, sharing the Agents' window; `concurrency` | Implemented |
| 5b | Model token usage, reply latency and cost | Implemented |
| 6 | Desktop Compare runs page | Implemented |
| 7 | Turn-based Agents, latency cost | Implemented |

[Section index](README.md) · [All documentation](../README.md)
