# Explain, sweep and review a study

These tools work on finished runs. They read what the runs recorded and change nothing.

| Question | Use |
| --- | --- |
| Why did the Agent place this order? | `decision_timeline()` |
| Is the result alpha, market exposure or costs? | `pnl_attribution()` |
| Which settings or prompts work best, and is the best one luck? | `run_sweep()` |
| Why do two runs differ? | `diff_runs()` |
| What should a reviewer see? | `model_risk_report()` |
| Does it still work on data that did not exist yet? | `research.forward.ForwardTest` |
| How do I turn signals into a portfolio? | `research.portfolio` |

## Decision timeline

```python
timeline = app.decision_timeline(profile_id, run_id)
for order in timeline['orders']:
    print(order['summary'], [read['summary'] for read in order['reads']], order['notes_after'])
```

An Agent works in rounds, one per scheduled turn. The timeline lists every step of each
round (model replies, data reads, orders, journal notes, control calls) and, for each
order, the reads and model replies before it in its round, the journal notes before and
after it, and its fills. Attribution is by order of events: a read listed before an
order shows the Agent had that information, not that it used it. Ask the Agent to write
its reason with `journal.write` to make the link explicit.

## PnL attribution

```python
figures = app.pnl_attribution(profile_id, run_id)
market = figures['market']
print(market['alpha_annual'], market['betas']['market'], market['r_squared'])
print(figures['costs'])
```

The run's per-bar equity returns are regressed on the equal-weight buy-and-hold return
of its symbols (ordinary least squares with t-statistics). A beta near the share of the
account held in the market and an alpha with a small t-statistic mean the result is
mostly market exposure. Supply other factor series, one value per bar of the run's
equity curve, with `factors={'SMB': [...], 'HML': [...]}`; collinear or constant
factors are reported instead of fitted.

Costs split the money paid on fills into commission, borrow and market impact, each
also in basis points of traded notional; `pnl['gross']` is the result before them.
Spread paid is not separated, because fills record the price paid, not the mid.

## Sweeps and heat maps

```python
from sim_platform.research.sweeps import heatmap, render_heatmap_svg

result = app.run_sweep(
    [recording],
    dict(name='rule', policy='rule'),
    dict(quantity=[1, 5, 10], hold_ns=[60 * 10**9, 600 * 10**9]),
    profile=profile_id,
)
svg = render_heatmap_svg(heatmap(result['rows'], 'quantity', 'hold_ns'))
```

The grid names fields of a suite entry by dotted path, such as `model.temperature` or
`settings.budget.max_tokens` for an Agent. For an Agent entry, `prompt` lists task
prompts; each is saved as a new revision of the Agent and run as its own combination.
Every combination runs on every recording on new accounts with the same seeds.

Picking the best cell of a grid is a search: each row's deflated Sharpe ratio counts
every combination as a trial, and the evaluation of each chunk of combinations
reports the probability of backtest overfitting. Prefer a region of good cells to a
single best cell, and confirm the choice on a recording the sweep did not use.

## Run diff

```python
diff = app.diff_runs(profile_id, first_run, second_run)
```

Lists each differing field of the execution settings, the rest of the configuration,
the task prompt, the model (including the versions served), the input file digests and
the code, and sets the main result figures side by side. Run IDs, file times and
prepared-data paths are ignored.

## Model risk report

```python
app.model_risk_report(profile_id, [agent_run, rule_run], path='/absolute/path/review.md',
                      baseline=rule_run)
```

One document per study for model validation: each run's execution assumptions in plain
words, its data and code digests from the [lineage](research-controls.md#prove-a-run-is-unchanged),
the Agent and model (versions served, calls, tokens, budget), result figures,
memorisation flags and the lineage verification, a comparison of the runs with
probabilistic and deflated Sharpe ratios when there are several, and the known limits.
A `.md` path writes Markdown; any other path writes the JSON report.

## Forward test in shadow mode

```python
from sim_platform.research.forward import CsvFeed, ForwardTest

test = ForwardTest(app, profile_id, account_id, CsvFeed('/vendor/aapl-daily.csv'),
                   '/absolute/path/forward', symbol='AAPL', method='rule',
                   settings=dict(quantity=10, hold_ns=5 * 86_400 * 10**9))
test.run(poll_seconds=3600)  # or call test.step() from your own scheduler
```

A forward test trades data as it arrives, the only test whose data did not exist when
the method was built. Whenever the feed has new bars, they are appended to a growing
recording (earlier bytes are never rewritten) and the method runs over just those bars
on the same simulated account, so positions and cash carry from segment to segment.
Each segment is an ordinary run with its own lineage, and `segments()` reads the
append-only arrival log: when each batch arrived (UTC), the recording's digest at that
moment and the run that traded it.

Forward tests are shadow-only: Market Lab has no broker connection, so orders always go
to the simulated execution. For an Agent pass `method='agent'`, its `agent_id`,
`revision`, complete settings and `configure=lambda experiment:
app.configure_experiment_model(experiment, profile)`. Method state does not carry
between segments; an Agent can carry its views in journal notes.

## Portfolio construction helpers

`sim_platform.research.portfolio` turns returns or signals into orders with plain
functions: `equal_weights`, `inverse_volatility`, `risk_parity`, `minimum_variance`
(long only) and `signal_weights`; `volatility_target` and `apply_limits` (per-name,
gross and net caps); `target_shares` (whole shares or lots at given prices) and
`rebalance_orders` (sells first, skipping changes below a minimum). Use them in a
notebook to build model-file targets, or copy the module into a model file's project.

[Section index](README.md) · [All documentation](../README.md)
