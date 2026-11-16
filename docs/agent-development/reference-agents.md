# Reference Agents

Market Lab ships Agents that follow published LLM trading-agent architectures, so you can
test those designs under the same execution rules, leakage guards and statistics as your
own Agent, rules and models. They are independent implementations from each paper's
description, not the authors' code, and use only the public SDK.

| Key | Architecture | Paper |
| --- | --- | --- |
| `trading_agents` | Technical, news and fundamentals analysts; a bull/bear debate; a risk manager; a trader | [TradingAgents](https://arxiv.org/abs/2412.20138) (Xiao et al., 2024) |
| `layered_memory` | Short, mid and deep memories ranked by recency, relevance and importance; a reflection after each decision | [FinMem](https://arxiv.org/abs/2311.13743) (Yu et al., 2023) |
| `manager_analyst` | Analysts report to one manager; risk control critiques each decision and rewrites the investment beliefs | [FinCon](https://arxiv.org/abs/2407.06567) (Yu et al., 2024) |

## Select one

In the Desktop, open **Agents**, choose **New Agent**, set **Implementation** to
**Python Agent** and pick one under **Or start from a reference Agent**. The source, the
tools it needs and its default parameters are filled in; values you type win.

From Python:

```python
for entry in app.reference_agents():
    print(entry['key'], entry['name'])
saved = app.import_reference_agent(profile_id, 'trading_agents', model_profile_ref='my-model')
```

The saved revision is an ordinary Agent: prepare its environment, then select it in an
experiment or an evaluation suite. Each decision makes several model calls, so pass a
longer `schedule` (for example one decision a day) unless you need intraday decisions.

## Paper settings or strict settings

Papers usually assume fills at displayed prices with no costs. Run each reference Agent
both ways to see how much of a result survives realistic frictions:

```python
from sim_platform.reference_agents import execution_preset

paper = execution_preset('paper')     # displayed prices, no costs
strict = execution_preset('strict')   # queue model, lognormal latency, fees, impact, borrow
experiment = app.open_experiment(profile_id, account_id, source=path, method='agent',
                                 agent_id=saved['agent_id'], revision=saved['revision'],
                                 settings=settings, execution_mode='live',
                                 execution_config=strict)
```

## Rehearse without model calls

`RehearsalProvider` stands in for the model: it reads one data tool per role, ends each
role, buys one share and finishes. It checks the plumbing (tools, data files, orders,
journal, Replay) at no cost; its results have no investment meaning.

```python
from sim_platform.models.providers.rehearsal import RehearsalProvider

app.configure_experiment_model(experiment, model_profile, provider=RehearsalProvider('AAPL'))
```

## Usage by role, journal and learning across runs

Every role labels its model requests, so an Agent log's usage
(`research.evaluation.agent_model_usage`) has `by_label`: replies and tokens per role, such
as `news analyst` or `trader`. The Agents write notes with the `journal.write` tool (the
risk manager's limits, lessons, beliefs); read them with `app.agent_journal(profile_id,
run_id)`.

`manager_analyst` continues learning across runs: take the beliefs its last run wrote and
pass them as the `beliefs` parameter of the next one.

```python
from sim_platform.reference_agents import latest_beliefs

beliefs = latest_beliefs(app.agent_journal(profile_id, run_id))
app.import_reference_agent(profile_id, 'manager_analyst', parameters={'beliefs': beliefs})
```

Use a training period for this and keep the evaluation period separate, as with any
learning method.

## What to expect

- **Model calls.** `trading_agents` makes about seven model calls per decision (three
  analysts, two debaters, risk, trader) plus their tool steps. Use turn-based pacing to
  judge decisions alone, or Live pacing to include the cost of thinking time.
- **Data.** Analysts whose tools are not granted, or whose data is missing (no news file,
  no `fundamentals_path`), are skipped or report `DATA_UNAVAILABLE`. See
  [news and fundamentals](projects.md#news-fundamentals-macro-data-and-filings).
- **Memory and beliefs** contain only what the Agent saw, so they cannot carry later
  information into an earlier decision. Reflection (`layered_memory`) and critique
  (`manager_analyst`) review each decision at the start of the next round, when its
  outcome is visible; a round ends when the trader finishes.
- **Parameters.** `trading_agents`: `debate_rounds` (default 1). `layered_memory`:
  `risk` (`averse`, `neutral`, `seeking`) and `memories` (default 8).

To build your own multi-role Agent, copy one of these projects from
`sim_platform/reference_agents/`. A role is a `TradingAgent` given only the tools its job
needs, plus a local tool named in `finish_tools` that ends its turn, for example
`report.submit`; see the [SDK guide](sdk.md).

[Section index](README.md) · [All documentation](../README.md)
