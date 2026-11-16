# Evaluate an Agent against baselines

An evaluation answers one question: does this Agent trade better than a rule, a model or a
person on the same market, and is the difference larger than chance? It puts saved runs
side by side, gives each figure a confidence interval and tests every entry against a
baseline. Use it from the Desktop's **Compare runs** page or from Python
([design](../architecture/agent-evaluation-design.md)).

## In the Desktop

Open **Compare runs** in the sidebar. Finished runs are grouped by what made their
decisions: an Agent revision, a rule with its settings, a model file or manual trading.
Tick a group to choose all its runs, or tick single runs. After the first choice, runs on
a different market or with different cash or execution settings are greyed out. Choose the
metric and the baseline; for each Agent group, enter its model's training cutoff if you
know it, and the model's price per million input and output tokens to see its cost.
**Evaluate** shows each group's mean and 95% interval, the difference from the baseline
with its p-value, the memory check and, for Agent groups, model usage. **Export report**
saves the full JSON report. The sections below explain each figure and are the same for Python.

## 1. Disguise the market

A language model may have seen the prices of a well-known stock in its training data. If
it has, a good result may only show memory, not trading skill. Disguise a price-bar file
before an Agent trades it:

```python
mapping = app.disguise_recording('/data/aapl.csv')
# {'output': '.../disguised/disguised-1a2b3c4d5e6f.csv', 'first_date': '2019-01-02',
#  'disguised_first_date': '2100-01-04', 'shift_days': 29589, 'price_scale': 1.2735}
```

The copy moves every date by whole weeks into the year 2100 (weekdays are kept) and
multiplies every price by one factor, rounded to the cent. Volumes and times of day are
kept. Every other column, such as `Adj Close`, is dropped. Daily returns are the same up
to that rounding.

- Use the copy for **every** method you compare, so all of them trade the same market.
- Give the experiment a neutral symbol such as `ASSET1`; the file itself has no ticker.
- Keep the returned mapping to yourself. Agent code is trusted local code: an Agent that
  reads files on your computer could find the original.
- Only price-bar CSV files can be disguised. ITCH recordings and news cannot.

### Disguise news and fundamentals too

A headline naming the company gives it away even when prices are disguised. Pass the
experiment's other inputs to the same call:

```python
mapping = app.disguise_recording('aapl.csv', news='aapl-news.json',
                                 fundamentals='aapl-fundamentals.csv',
                                 names=['Apple', 'AAPL'], symbol='ASSET1')
# mapping['news_output'], mapping['fundamentals_output']
```

Listed names become `the company` (choose with `alias`), dates and years in text move by
the same shift as the prices, and fundamentals rows take the neutral symbol.

### Choose symbols without survivorship bias

Testing today's index members over past years keeps only the survivors. With a membership
file (`symbol, start_date, end_date, reason`), pick the members on the first test day and
check a chosen set:

```python
from sim_platform.research.universe import load_membership, members_on, survivorship_check

spells = load_membership('index-members.csv')
symbols = members_on(spells, '2015-01-02')
check = survivorship_check(spells, my_symbols, '2015-01-02', '2019-12-31')
# check['missing_leavers'] lists delisted members your test leaves out
```

## 2. Produce the runs

An Agent's model answers differently each time, so one run is one sample, not its
performance. Ten runs is a reasonable start. Run the Agent and its baselines together in
a suite; each run gets its own new account:

```python
with app.open_batch() as batch:
    suite = batch.evaluate_suite(
        [mapping['output']],
        [
            dict(
                name='Agent v3',
                policy='agent',
                agent_id='trend-reader',
                revision=3,
                settings=agent_settings,  # includes start_ns and end_ns
                model=dict(provider='openai-responses', model='your-model',
                           credential_ref='personal'),
                repetitions=10,
            ),
            dict(name='Buy, hold 1 day', policy='rule', hold_ns=86_400 * 10**9),
            dict(name='LSTM', policy='model', model=dict(model_file='/models/lstm.py')),
        ],
        baseline='Buy, hold 1 day',
        symbol='ASSET1',
        profile=profile_id,  # the profile that holds the saved Agent
        concurrency=4,
        training_cutoffs={'Agent v3': '2024-06-01'},
    )
```

`repetitions` defaults to 10 for an Agent and to 1 for a rule or model file, which
repeat exactly. Store the model's key first with `app.set_model_credential()`.

### Real time and concurrency

An Agent plays in real time: one second of market time takes one second, and the market
keeps moving while the model answers, as it would in live trading. A one-hour window
therefore takes an hour per run. When a suite has Agent entries, every rule and model-file
run also starts at the Agents' `start_ns` and stops at their `end_ns` (at the first event at
or after it), so all runs cover the same market time.

`concurrency` plays up to that many runs of an Agent at the same time (1 to 16, default 1).
Each run still plays in real time on its own account and does not affect the others; only
the total wait is shorter. Ten one-hour runs take about ten hours one after another and
about three hours with `concurrency=4`. Markets are prepared one at a time before the runs
play. Concurrent runs share your computer and your model provider: if the provider slows
down or limits requests, every Agent answers later than it would alone and its results
change. Choose a level your provider handles without slowing down.

You can also run Agents yourself, in the Desktop or with
`open_experiment(method='agent', ...)`, and evaluate their saved runs with the suite's.

A suite accepts up to 20 recordings and evaluates each one separately. Repeating runs on one
recording measures a method's own randomness on that market, not how it does in other
markets; to judge robustness, compare results across recordings.

## Turn-based Agents

Live results mix two things: how good an Agent's decisions are, and how fast it makes
them. A **turn-based** Agent separates them. The market waits while the Agent works:
from the moment its work starts until it finishes, market time stands still, however long
the model takes to answer. When the Agent is idle, the market moves straight to its next
turn: its next scheduled work, the time it asked to wait for, or the next market event that
could wake it. Every market event in between is processed in order, exactly as in Live.

```python
agent_settings = dict(agent_settings, speed='turn')   # '1x' is Live
```

In the Desktop, choose **Turn-based** under **Pacing** in New experiment → Account &
execution. In Python, use it in `open_experiment(method='agent', ...)` or in a suite
entry's `settings`. The workspace badge and Compare runs say which pacing a run used.

| | Turn-based | Live |
| --- | --- | --- |
| Measures | Decision quality | Decision quality and speed together |
| Model reply time | Costs no market time | The market moves while the model answers |
| Wall-clock time | Usually much shorter than the window | Equal to the window |
| Best for | Screening prompts and models; long windows; many repetitions; strategies that decide daily or hourly | Final validation of intraday, speed-sensitive strategies |

A practical research loop:

1. **Screen** turn-based: try prompts and models over long windows and many repetitions.
2. **Validate** in Live: run the best few on a shorter window.
3. **Report** both, with the **latency cost**: the Live mean minus the turn-based mean on
   the same market and window.

```python
from sim_platform.research.evaluation import latency_cost

cost = latency_cost(live_evaluation, turn_evaluation, 'Agent v3', metric='total_return')
# {'live_mean': ..., 'turn_mean': ..., 'cost': ..., 'interval': [low, high], ...}
```

A negative cost on total return means thinking time lost money. Rules
and model files already decide without delay, so comparing them with a turn-based Agent
compares decisions alone.

Rules of the mode:

- An evaluation refuses to mix Live and turn-based Agent runs, since they answer different
  questions. Their group names in Compare runs say which they are.
- Turn-based results overstate a speed-sensitive strategy: in live trading the market does
  not wait.
- The Agent still sees only data up to the current time; nothing from the future is
  visible while the market waits.
- An order or cancel made while the market waits executes at that same instant (the clock
  moves on by one nanosecond so the order is processed).
- A model reply that has not arrived within the attempt timeout in real time is timed out,
  as in Live. Reply time is not reported for turn-based runs.

## 3. Evaluate the saved runs

```python
report = app.evaluation(
    profile_id,
    {
        'Agent v3': agent_run_ids,
        'Buy, hold 1 day': [rule_run_id],
        'LSTM': [model_run_id],
    },
    baseline='Buy, hold 1 day',
    metric='total_return',
    training_cutoffs={'Agent v3': '2024-06-01'},
)
```

All runs must have traded the same recording, from the same start, with the same starting
cash, fee, latency and short-selling setting. Otherwise the call fails and names the first
setting that differs, for example `execution.fee_per_share differs between run … and run …`.
Live and Controlled pacing may differ.

### Figures for every run

| Figure | Definition |
| --- | --- |
| `total_return` | Final equity over starting cash, minus one; equity marks holdings at the last archived close |
| `max_drawdown_ratio` | Largest fall of equity from an earlier peak, as a share of that peak |
| `sharpe_ratio` | Annualised mean over standard deviation of daily returns ([results](results.md)) |
| `sortino_ratio` | Annualised mean daily return over the downside deviation: the root mean square of every return's shortfall below zero, gains counted as zero |
| `win_rate` | Share of closed round trips with a profit |
| `turnover` | Traded value over starting cash |
| `exposure` | Share of archived bars that end with a position open |
| `round_trips` | Closed round trips |
| `fees` | Fees paid during the run |

Agent runs also have model figures, read from the Agent's operation log. Other methods
have none.

| Figure | Definition |
| --- | --- |
| `model_calls` | Model requests sent, retries included |
| `failed_calls` | Requests that failed and were retried or given up |
| `input_tokens`, `output_tokens` | Tokens the provider reported, summed over the run; `None` if it reported none |
| `mean_latency_seconds` | Mean time from sending a request to receiving its answer |
| `model_cost` | `input_tokens` and `output_tokens` priced at `token_prices`, in USD |

Pass `token_prices={'input': 2.5, 'output': 10}` (USD per million tokens) to
`evaluation()` or `evaluate_suite()` to get `model_cost`. Market Lab ships no price list,
because provider prices change. Only token counts leave the Agent; the model's replies
are not recorded here.

A figure is `None` when it cannot be computed, for example a Sharpe ratio from fewer than
two daily returns. Risk-free rate and target return are zero.

### Statistics

For each entry and figure the report gives `n`, `mean`, `stdev`, `min`, `max` and
`interval`: a 95% percentile bootstrap interval of the mean from 10,000 resamples.

Against the baseline, on `metric`:

- `difference`: entry mean minus baseline mean, with its bootstrap `interval`.
- `p_value`: two-sided. With a baseline whose values are all equal (one run, or a
  deterministic rule), a **sign-flip test** of the entry's values minus that constant.
  When the entry does not vary either, the difference is exact and no test is made:
  `p_value` is `None` and `deterministic` is `true`.
  Otherwise a **permutation test** that relabels runs between the two groups. Both are
  exact when there are at most 10,000 arrangements (`exact: true`).
- `p_value_holm`: the p-value adjusted with Holm's method across all entries, because
  testing several entries against one baseline is several tests. Read this one.
- `insufficient: true`: fewer than 5 runs. The numbers are shown but should not be relied on.

No overall score is computed. The same runs, `seed` and `resamples` always give the same
report.

### Selection and overfitting

If you tried 40 prompts and kept the best, its Sharpe ratio is partly luck. Tell the
evaluation how many configurations led to each entry with `trials`:

```python
report = app.evaluation(profile_id, entries, baseline='Rule', trials={'Agent v7': 40})
```

An entry whose runs all come from one saved Agent counts that Agent's saved revisions as
its trials when you give none; the report's `trial_sources` says where each count came
from (`given`, `agent revisions` or `one`). Each entry also lists the `served_models`
the provider reported, and the report warns when one entry mixes model versions.

Each entry's `selection` gives figures on the per-bar returns of its mean equity curve:

- `probabilistic_sharpe`: the probability that the true Sharpe ratio is above zero,
  allowing for the number of bars, skewness and fat tails.
- `deflated_sharpe`: the same probability measured against the Sharpe ratio the best of
  `trials` worthless strategies would reach. Above 0.95, the result survives the search.
  With one trial it equals `probabilistic_sharpe`.

The report's `overfitting` treats the entries as the candidates you chose between and
gives the probability of backtest overfitting (PBO): how often the entry that is best on
half of the time blocks ranks below the median on the other half. It needs two or more
entries with equally long curves. The [methodology](../reference/methodology.md) gives the
formulas, and `sim_platform.research.overfitting` offers walk-forward and purged k-fold
splits for your own model training.

### Walk-forward windows

Tune only on the past and test on what follows:

```python
windows = app.split_recording(source_path, '/absolute/path/windows',
                              train_days=250, test_days=60, gap_days=5)
tests = [w['test'] for w in windows]
with app.open_batch() as batch:
    report = batch.evaluate_suite(tests, entries, baseline='Rule', profile=profile_id)
```

Each window has a `train` and a `test` CSV; no test date precedes the end of its training
window, and `gap_days` leaves room for labels that look ahead.

### Benchmark

`evaluate_benchmark()` runs your entries against fixed baselines (buy and hold, a one-day
rule, a 20-day moving average) on five generated regimes: uptrend, downtrend, range,
volatile and crash. The recordings are byte-identical for a given seed and dated in 2100,
so anyone can re-run the same benchmark and no model can remember it:

```python
with app.open_batch() as batch:
    report = batch.evaluate_benchmark('/absolute/path/benchmark', entries, profile=profile_id)
print(report['benchmark'])   # {'version': 1, 'seed': 2026, 'days': 252, 'regimes': [...]}
```

A full run plays each entry on all five recordings and takes minutes per entry; pass
`regimes=['crash']` or a shorter `benchmark_days` to try it first.

### Memorisation flags

Each Agent run carries one flag; rule, manual and model-file runs carry none.

| Flag | Meaning |
| --- | --- |
| `disguised` | The data's dates lie in the future: a disguised recording |
| `after-cutoff` | Every date is after the training cutoff you gave for that entry |
| `possible-memorisation` | Real dates on or before the cutoff |
| `cutoff-unknown` | Real dates and no cutoff given for that entry |
| `dates-unknown` | An ITCH recording, which carries no date |

The report's `warnings` repeat the count of runs that may rely on memory, and note when
runs covered different market-time windows (an Agent stops at its chosen end; other
methods run to the end of the recording).

## Export results for your own analysis

`sim_platform.research.frames.report_tables(report)` turns a saved run report into flat
`fills`, `equity` and `figures` rows; `report_frames()` returns them as pandas DataFrames
and `export_report_tables(report, folder, file_format='parquet')` writes CSV or Parquet
files. Price bars stored as Parquet convert with
`sim_platform.market.parquet.convert_parquet_bars()`. Parquet needs
`pip install "market-lab[parquet]"` and DataFrames `pip install "market-lab[dataframes]"`.

## Limits

- Runs saved before model usage was recorded have model calls but no reply times or
  tokens.
- Each decision is saved as it happens, so a suite takes roughly as long as running each
  experiment in Controlled mode one after another.

[Section index](README.md) · [All documentation](../README.md)
