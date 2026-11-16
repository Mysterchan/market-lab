# Trade several symbols in one experiment

One experiment can trade several symbols from one account: one cash balance, one position
per symbol, one shared timeline. The manual, rule and model methods run on several symbols
in Controlled mode; manual trading and Agents also run in Live (real-time or turn-based)
mode. The Desktop offers manual trading and model files; rules and Agents on several
symbols are opened from Python. The design is in
[the design](../architecture/multi-symbol-design.md).

## In the Desktop

1. **New experiment**: choose the first recording and symbol as usual, then **Add another
   symbol** for each further one, with its own recording. Choose the same ITCH file on each
   row when it holds every symbol. Only **Manual trading** and **Model file** are offered,
   and playback is Controlled. The first recording's synthetic book applies to every CSV.
2. **Workspace**: the switch above the chart (for example **AAPL | MSFT**) chooses the
   symbol that the chart, market depth and order ticket show. An order goes to the symbol
   the ticket shows. Orders and fills list their symbol; **Position** is the shown symbol's.
   Portfolio value and net P&L cover the whole account, each holding at its own best bid
   (or best ask for a short).
3. **Decision maker**: in the Desktop an experiment on several symbols can only switch to
   Manual; run rules on several symbols from Python (below).
4. **Results**: the equity curve, maximum drawdown and Sharpe ratio value every holding at
   its own symbol's close ([how](results.md)). The price chart has the same symbol switch.

## Open one from Python

Give the symbols in the order you want, and one source per symbol: one CSV file of price
bars each, or a single ITCH file that contains every symbol.

```python
experiment = app.open_experiment(
    profile, account,
    source=['/data/aapl.csv', '/data/msft.csv'],       # or one ITCH path for all
    execution_mode='controlled',
    settings=dict(symbols=['AAPL', 'MSFT'], start_mode='first_book'),
)
experiment.order('a1', 'buy', 10, 1_000_100, symbol='AAPL')
experiment.order('m1', 'buy', 20, order_type='market', symbol='MSFT')
experiment.step()
```

`order(...)` and `cancel(...)` work as for one symbol; name the `symbol`. The market view
shows the book of the symbol whose event came last. The same call with
`execution_mode='live'` trades several symbols in real time.

### Rules

`method='rule'` (buy, hold for `hold_ns`, sell) and `method='hold'` run the same rule on
every symbol separately: each keeps its own state, and its orders are named after it
(`rule-buy-AAPL`, `rule-buy-MSFT`), so the symbols never share a decision.

### Agents

Add `symbols` to the Agent settings, starting with `symbol`, and give one source per symbol:

```python
settings = dict(agent_settings, symbol='AAPL', symbols=['AAPL', 'MSFT'])
experiment = app.open_experiment(profile, account, source=['/data/aapl.csv', '/data/msft.csv'],
                                 method='agent', agent_id=agent_id, revision=revision,
                                 settings=settings, execution_mode='live')
```

Every market tool takes any of these symbols (`market.quote(symbol='MSFT')` reads MSFT's
own book and trades), orders may name any of them, `control.wait` may wait on any of them,
and `environment.describe` lists them as `symbols`; inside the Agent,
`context.settings['symbols']` has the list. Replay rebuilds every symbol's market and
recalculates the run without the Agent. One news file serves every symbol: `news.list`
shows each record from its `available_time_ns`, as for one symbol. Fundamentals and
documents work as for one symbol.

## The timeline

- Events are processed by market time. Events at the same nanosecond go in the order the
  symbols were given, then in each source's own order. So with `['AAPL', 'MSFT']`, every
  AAPL event at 09:30:00 comes before MSFT's.
- CSV files that start on different dates are placed on one calendar: market day 0 is the
  earliest first date. A symbol with no bars yet simply has no book, and orders that need
  its quote wait or are rejected as usual.
- `first_book` starts once every symbol has shown a two-sided book.
- An order fills only against its own symbol's book, on that symbol's book changes.

## Money

Cash is shared. Positions, realized P&L and fees are account totals, exactly as for one
symbol; each position keeps its own [average cost](results.md#how-realized-pl-is-computed).
There is no limit on total exposure: buying power is the same cash rule as for one symbol,
and with `allow_short=True` each symbol's short [margin](../reference/orders.md#margin-rule)
is added up.

## A model on several symbols

`decide(bars, account, context)` then receives everything by symbol:

| Argument | Contents |
| --- | --- |
| `bars` | `{symbol: [completed bars]}` |
| `account` | `cash`, `available_cash`, `equity`, `positions` `{symbol: shares}`, `average_costs`, `open_orders` (each with `symbol`) |
| `context` | `symbols`, `time_ns`, `date`, `bar`, `quotes` `{symbol: {bid, ask}}`, `parameters` |

It returns `None`, `{"targets": {"AAPL": 100, "MSFT": 0}}`, which applies the
[target rule](model-policies.md#how-a-target-position-becomes-orders) to each symbol, or
`{"orders": [...]}` where every order names its `symbol`. It is asked once per bar.

## Results and Replay

`Platform.replay_run()` prepares every source again, rebuilds the same timeline and
verifies the run. On the result page, round trips, win rate, holding time and turnover are
computed per symbol and added up. The run saves every symbol's recent price bars and daily
closes, from which the equity curve and the Sharpe ratio value the whole account.

## Not yet

- A different synthetic book per CSV.

[Section index](README.md) · [All documentation](../README.md)
