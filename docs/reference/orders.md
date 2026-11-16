# Orders, market orders and short selling

This page is the financial contract for simulated orders. Every method (manual, rule,
model, Agent) and Replay uses the same rules through the shared execution path. Money is
integer USD ten-thousandths.

## Limit orders

A limit order is accepted when the market is open, the symbol is the experiment's and the
account can pay for it (see [reservations](#reservations)). It becomes eligible after the
configured latency and on a later market event than the one it was accepted on. It fills
on a book change whose opposite best price reaches its limit, taking displayed shares best
price first, never more than the displayed size not already taken by an earlier simulated
order. Unfilled shares keep working until filled, cancelled or the experiment ends.

## Execution models and costs

`ExecutionConfig` sets every rule below for a run, and the run saves it.

| Setting | Default | Effect |
| --- | --- | --- |
| `model` | `displayed-limit-v1` | Matching rule (below) |
| `latency_ns` | 0 | Delay from acceptance until an order may first match |
| `latency_jitter_ns` | 0 | Extra delay drawn from 0 to this value, fixed by the order ID |
| `latency_sigma_milli` | 0 | Instead of jitter, a lognormal delay with median `latency_ns` and sigma in thousandths |
| `fee_per_share` | 0 | Fee on every filled share, reserved in advance for buys |
| `impact_bps` | 0 | Square-root market impact coefficient, in basis points |
| `borrow_bps_per_year` | 0 | Annual borrow rate charged when a short is covered |

With `displayed-limit-v1` only an order whose limit reaches the opposite best price
fills. A resting order at its own side never fills, which understates fills of passive
strategies. `displayed-limit-queue-v1` adds a queue model: a resting order joins the back
of its price level, executions at that level (ITCH E and C messages) first use up the
shares that were displayed ahead of it, and then fill it at its limit. Cancellations are
assumed to leave from behind the order. A level deeper than the recorded book depth
cannot be observed, so orders there wait. Such fills carry `liquidity: passive`.

Impact makes a fill that takes a fraction f of the displayed opposite side pay
`impact_bps × √f` basis points worse than the quoted level, rounded against the trader
and never beyond the limit. The fill records both `price` and `quoted_price`.

```python
from sim_platform.execution import ExecutionConfig

realistic = ExecutionConfig(
    model='displayed-limit-queue-v1', latency_ns=2_000_000, latency_jitter_ns=3_000_000,
    fee_per_share=5, impact_bps=50,
)
```

Pass it as `execution_config` to `open_experiment()` or `open_agent_experiment()`. Runs with
different settings cannot be evaluated together.

## Market orders

A market order has no limit chosen by the trader. The platform gives it a **protection
price** when it is accepted and then treats it exactly like a limit order at that price:

| Side | Protection price |
| --- | --- |
| Buy | Best ask at acceptance plus 5%, rounded up to a whole money unit |
| Sell | Best bid at acceptance minus 5%, rounded down, and at least one money unit |

- With no ask (for a buy) or no bid (for a sell) displayed at acceptance, the order is
  rejected: there is no price to protect.
- It fills at the displayed prices it takes, not at the protection price. Over the next
  book change it takes every displayed level up to the protection price, as far as the
  displayed size allows.
- Shares it cannot fill within the protection price keep working at that price, like an
  unfilled limit order, until filled, cancelled or the experiment ends. They do not chase
  the market beyond 5%.
- Cash for a buy is reserved at the protection price plus fees, so a market buy needs
  about 5% more available cash than its expected cost. The unused part is released as it
  fills.
- The order type and the 5% protection are recorded with the order's acceptance. Replay
  recomputes the protection price from the same recorded book and must get the same value.

Ways to send one: the Desktop order ticket's **Market** type,
`experiment.order(client_id, side, quantity, order_type='market')`, a policy action
`{"kind": "submit_market", "order": {"client_id", "symbol", "side", "quantity"}}`, the
Agent tool `orders.submit_market`, and a model's order `{"side", "quantity", "type": "market"}`.

## Short selling

Short selling is off unless the experiment turns it on: `open_experiment(...,
allow_short=True)`, the Desktop's **Allow short selling** setting, or `--allow-short` on the
command line. It is available for manual, rule, hold and model experiments; Agent
experiments do not offer it yet. With it off, a sale larger than the shares held is
rejected, as before. The setting and the margin rule below are recorded with the run as
`short_selling` in its configuration.

### Margin rule

The rule follows Regulation T's initial margin of 50%, simplified:

1. A sale may be larger than the shares held and not already reserved. The excess opens or
   adds to a short position.
2. **Initial margin.** Opening shares short needs available cash of at least 50% of their
   value at the order's limit price, plus the fees for those shares. That amount is
   reserved while the order is open.
3. **Proceeds stay as collateral.** When a short sale fills, its proceeds (after the fee)
   are added to cash, and 150% of the short position's proceeds after fees is held back
   from available cash: the proceeds themselves plus the 50% margin. Buying power for other
   orders is therefore cash minus that hold.
4. **Covering.** A buy against a short position first covers it. The hold shrinks in
   proportion to the shares covered. A buy larger than the short closes it and opens a
   long position with the rest.
5. **No margin calls.** There is no maintenance margin and no forced buy-in. If the price
   rises far enough, equity can fall below zero; results show that loss in full. The
   result page's figures still apply.
6. **Borrow cost is optional.** Shares are always available to borrow. With
   `borrow_bps_per_year` above zero, covering a short pays that annual rate on the
   short's proceeds (after fees) for the market time it was open, share-weighted when
   it was built in several sales. The charge is added to the covering fill's fee and
   recorded as `borrow_fee`; a short still open at the end is not charged. There is no
   dividend or rebate on short proceeds.

### Cost basis and realized P&L for shorts

The ledger keeps one cost figure per position. For a short position it is the proceeds
received, after fees, stored as a negative number. Covering part of a short releases the
same share of those proceeds, rounded toward zero, or all of them when the short is
closed. The cover's realized P&L is the proceeds released minus the cover's value and fee.
This mirrors the long-side [average cost rule](../user-guide/results.md#how-realized-pl-is-computed),
so round trips on the result page work for shorts too: a short round trip starts when the
position leaves zero by selling and ends when it returns to zero.

## Reservations

Open orders hold what they might need, and new orders can only use what is left:

- an open buy holds its unfilled shares × (limit price + fee per share);
- an open sell holds the shares it would sell from the position;
- with short selling on, the part of an open sell beyond those shares holds 50% of its
  value at the limit price plus its fees;
- a filled short holds 150% of its proceeds after fees, as above;
- a buy that covers a short may use the share of that hold it releases.

Without short positions or short orders, every amount is exactly what it was before short
selling existed.

[Section index](README.md) · [All documentation](../README.md)
