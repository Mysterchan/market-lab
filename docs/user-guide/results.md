# Read a saved result

Open a finished experiment from **Experiments** to see its result page. All figures are
computed in Python (`sim_platform/desktop/presentation.py`) from the run's recorded fills
and saved market data. They describe the simulation; they are not broker statements.

## Account figures

| Figure | Definition |
| --- | --- |
| Portfolio value | Cash plus shares held, valued at the last observed best bid |
| Net P&L | Portfolio value minus the starting cash |
| Realized P&L | From the ledger: proceeds of each sale after its fee, minus the cost it removes from the position (see below) |
| Max drawdown | Largest fall from a peak of the equity curve, over the archived price bars |
| Fills, Fees | Number of fills and fees charged in this run |

## How realized P&L is computed

The ledger keeps one cost figure per position, the **average cost** method:

- a buy adds its value **and its fee** to the position's cost;
- a sale removes `cost × shares sold ÷ shares held` of that cost, rounded down to a whole
  money unit, or all of it when the sale empties the position;
- the sale's realized P&L is its value minus its fee minus the cost it removed.

So both fees of a round trip are inside realized P&L. The trade statistics below use this
same rule, so the P&L of the round trips adds up to the realized P&L.

## Trade statistics

| Figure | Definition |
| --- | --- |
| Round trip | Starts with the fill that takes the position from zero shares and ends with the fill that brings it back to zero. Adding to or partly reducing the position stays inside the same round trip. A position still open at the end is not counted |
| Win rate | Round trips whose P&L is above zero, divided by all closed round trips. A round trip with exactly zero P&L counts as not winning |
| Average holding time | Mean time from a round trip's first fill to its last, in market time, so nights and weekends between sessions count |
| Turnover | Total value of all fills, buys and sells, divided by the starting cash. Buying and later selling the whole starting cash once gives 2.00× |
| Sharpe ratio | Mean of daily returns divided by their sample standard deviation, times √252. The risk-free rate is taken as zero |
| Sortino ratio | Mean of daily returns divided by their downside deviation (the root mean square of each loss, gains counted as zero), times √252. "—" when no day lost money |

For the Sharpe ratio, the account is valued at the end of each market day as cash plus
shares at that day's last trade price. The first daily return is measured from the
starting cash. At least two daily returns are needed, so a one-day ITCH recording shows
"—", as does a run whose returns never vary. With fewer than 20 daily returns the figure
is shown with "not reliable": a handful of days says little about risk-adjusted return. Days are the recording's market days; a daily
price-bar file has one per trading day, which is why 252 is used.

Short positions are paired the same way: a short round trip starts with the sale that
takes the position below zero and ends with the buy that brings it back; its P&L follows the
[short cost basis](../reference/orders.md#cost-basis-and-realized-pl-for-shorts). Portfolio
value marks long shares at the best bid and short shares at the best ask.

On [several symbols](several-symbols.md) the account is valued the same way with every
holding at its own symbol's close. A symbol with no bar on a day keeps its last close, and
the day's fills count up to the latest close of that day. The equity curve starts when every
symbol has a bar and moves each time any symbol's bar closes. Runs on several symbols saved
before every symbol's closes were recorded show "—" for the Sharpe ratio and no curve.

These statistics need a run that started with no shares. A run that inherited holdings
shows "—", because its starting cost basis is not part of the run. Runs saved before daily
closing prices were recorded show "—" for the Sharpe ratio; the other figures use only the
fills.

[Section index](README.md) · [All documentation](../README.md)
