# Trade with your own model

The model decision method runs a Python file you write, such as a trained neural network,
and trades on what it returns. It sits next to the manual, rule and hold methods and runs
in Controlled mode, including on recordings that cover many days.

The platform does not install torch, onnx, scikit-learn or anything else for you. Your file
imports what it needs. Prepare those packages the same way as for an Agent project (see
[below](#packages-such-as-torch)).

## The function

Write a `.py` file that defines `decide`:

```python
def decide(bars, account, context):
    ...
    return {"target_position": 100}
```

An optional `setup(parameters)` is called once, before the first decision. Load model
weights there. The file runs in its own process for the whole experiment, so module-level
state, such as a loaded network, is kept between calls.

### What it receives

`bars` is a list of completed bars, oldest first, each a dict:

| Key | Meaning |
| --- | --- |
| `time_ns` | Bar start, in market time (nanoseconds from midnight of the recording's first day) |
| `date` | ISO date for a price-bar recording, otherwise `None` |
| `open`, `high`, `low`, `close` | Prices in dollars, as floats |
| `volume` | Shares traded |

The bar in progress is never included, so the model cannot see part of a bar or any later
data. How many bars you get is the lookback setting, 50 by default. Early in a recording
there may be fewer, or none.

`account` holds `cash` and `available_cash` (dollars), `position` (shares held),
`average_cost` (dollars, or `None`), `equity` (cash plus the position at the best bid, or
`None` without a bid) and `open_orders` (each with `client_id`, `side`, `quantity`,
`filled` and `limit_price`).

`context` holds `symbol`, `time_ns`, `date`, `bar` (the bar length, such as `1d` or `60s`),
`bid` and `ask` (dollars, or `None` when that side is empty) and `parameters` (the values
you supplied).

Prices are floats for your convenience only. All accounting stays in exact integer money.

### What it returns

| Return value | Effect |
| --- | --- |
| `None` | Change nothing |
| `{"target_position": n}` | Hold `n` shares (whole number, 0 or more), by the rule below |
| `{"orders": [...], "cancel_open_orders": false}` | Send these limit orders |

Each order is `{"side": "buy" or "sell", "quantity": shares, "limit_price": dollars}`, or a
market order `{"side": ..., "quantity": ..., "type": "market"}` priced as described in
[orders](../reference/orders.md#market-orders).
`limit_price` may be a number or a decimal string. It is rounded half-to-even to the nearest
$0.0001. `cancel_open_orders: true` first cancels the model's own unfilled orders.

Anything else, an exception, or a call that takes longer than the timeout (10 seconds by
default) fails the run. The run's error shows the model's message.

## When the model is called

The model decides once per bar, at the first market event of each new bar:

| Recording | Bar |
| --- | --- |
| Daily price bars | One calendar day; the decision is at the next day's open |
| Intraday price bars of whole minutes | That length, such as `300s` |
| ITCH, or price bars shorter than a minute | One minute |

So a daily model sees yesterday's completed bar at today's open and trades at today's
prices. Bars longer than a minute are grouped by their start time; files whose bars start at
round multiples of their length (09:30, 09:35, …) are grouped exactly.

## How a target position becomes orders

This rule changes fills and P&L, so it is fixed and recorded with every run as
`target_order_rule: cancel-own-open-then-limit-at-touch`:

1. Cancel every order this model sent that is still open, including partly filled ones.
   Orders you placed by hand are left alone.
2. Compare the target with the shares now held.
3. If more are wanted, send one buy for the difference at the best ask. If fewer, send one
   sell for the difference at the best bid. If that side of the book is empty, send nothing;
   the model is asked again at the next bar.

The order is an ordinary limit order. It fills like any other order on the next book change,
up to the shares shown at that price and later prices within the limit. A large target can
therefore fill partly; the rest is cancelled at the next decision and re-sent at the new
touch. Buying at the ask and selling at the bid means every round trip pays the spread.
Fees and latency come from the experiment's execution settings. A buy that the account
cannot pay for is rejected by the normal funds check, not resized.

Targets below zero need an experiment with short selling allowed (`allow_short=True`);
they then sell short under the [margin rule](../reference/orders.md#margin-rule).
Otherwise they fail the run.

## Recorded with the run

The run's decision policy records the model file's path and SHA-256, the bar length, the
lookback, your parameters and the target-order rule. Each decision record stores the bars
the model saw and the orders that resulted, so Replay never needs to run the model again.
`Platform.replay_run()` verifies a finished model run this way; the model file is not
imported.

## Run it

Desktop: New experiment → Who makes the decisions? → Model file. Choose the file, the
lookback and, under Model options, parameters as JSON and an optional interpreter.

Python:

```python
experiment = app.open_experiment(
    profile, account, source='/data/aapl-daily.csv', method='model',
    execution_mode='controlled',
    settings=dict(model_file='/models/ma/model.py',
                  model_parameters={'window': 5, 'shares': 100},
                  lookback_bars=50, model_timeout_seconds=10))
while experiment.runner.active:
    experiment.step()
```

Command line:

```bash
python -m sim_platform evaluate --input /data/aapl-daily.csv --policy model \
  --model-file /models/ma/model.py --model-parameters '{"window": 5}'
```

The [standard-library example](../../examples/models/moving_average.py) needs no packages.

## Packages such as torch

Put your model in a folder with `model.py`, a `requirements.txt` and, optionally, a
`model.json` such as `{"python": "3.12"}`. Prepare it with the same wheel-only installer
Agent projects use, then pass its interpreter:

```python
job = app.prepare_model_environment('/models/lstm', allow_download=True)
job.wait(900)
python = app.model_environment('/models/lstm')
settings = dict(model_file='/models/lstm/model.py', model_python=python)
```

Without `model_python` the model runs on the App's own Python, which is enough for the
standard library. The model process is a separate process, not a security sandbox: run only
code you trust.

[Section index](README.md) · [All documentation](../README.md)
