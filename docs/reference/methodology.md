# Methodology

This page states what Market Lab assumes when it turns decisions into results, how it
tests those results, and which biases remain. Cite it with the version you used.

## Simulated execution

Every method (manual, rule, model file, Agent) and Replay share one execution path, so
the same orders on the same market give the same fills. The rules are in
[orders](orders.md); in short:

- Orders take only **displayed** liquidity from the recorded book. Two simulated orders
  never take the same displayed shares, and consumed shares are not replenished until the
  recorded size grows again.
- Simulated orders do not change the historical book. Large orders therefore see more
  liquidity than they would in reality; `impact_bps` charges for this with a square-root
  model, the usual first approximation for the cost of trading a fraction of depth.
- `displayed-limit-v1` never fills a resting order. `displayed-limit-queue-v1` fills it
  only after the shares displayed ahead of it have traded, and assumes cancellations
  leave from behind it. Both are conservative toward passive strategies.
- Latency is a fixed delay plus an optional uniform jitter, or a lognormal delay with a
  heavy right tail, both fixed by the order ID; an order can never fill on the market
  event it was accepted on.
- Short sales follow a fixed margin rule with unlimited borrow and an optional annual
  borrow rate charged at cover. A short still open at the end is not charged in the
  cash ledger; its accrued borrow is reported as `borrow_accrued` in the run's costs
  (`pnl_attribution()`), as an open position is valued at the end.

## Statistics

Evaluation compares groups of saved runs (see [evaluation](../user-guide/evaluation.md)).

| Question | Method |
| --- | --- |
| How uncertain is an entry's mean? | 95% percentile bootstrap interval, 10,000 resamples |
| Is an entry different from the baseline? | Two-sided permutation test, or sign-flip test against a constant baseline |
| Several entries against one baseline | Holm adjustment of the p-values |
| Is a Sharpe ratio above zero? | Probabilistic Sharpe ratio (Bailey and López de Prado, 2012) |
| Does it survive the search that found it? | Deflated Sharpe ratio (Bailey and López de Prado, 2014) |
| How often does picking the best backtest mislead? | PBO by combinatorially symmetric cross-validation (Bailey et al., 2017) |
| Training a model without leakage | Walk-forward and purged k-fold splits with embargo (López de Prado, 2018) |

The probabilistic Sharpe ratio is

```latex
\mathrm{PSR}(SR^*) = \Phi\left(\frac{(\widehat{SR} - SR^*)\sqrt{n-1}}{\sqrt{1 - \hat\gamma_3 \widehat{SR} + \frac{\hat\gamma_4 - 1}{4}\widehat{SR}^2}}\right)
```

with per-period Sharpe ratio, skewness γ3 and kurtosis γ4 of n returns. The deflated
Sharpe ratio sets SR* to the expected maximum of N trials with Sharpe variance V:

```latex
SR^* = \sqrt{V}\left((1-\gamma)\,\Phi^{-1}\!\left(1-\tfrac{1}{N}\right) + \gamma\,\Phi^{-1}\!\left(1-\tfrac{1}{N e}\right)\right)
```

where γ is the Euler–Mascheroni constant. Market Lab estimates V from the Sharpe
ratios of the evaluated entries.

## Known biases and how to address them

| Bias | Status |
| --- | --- |
| Look-ahead in market data | Prepared data is clock-limited: decisions see only events up to the experiment time |
| Look-ahead in text | News records become visible at `available_time_ns`; use the publication time, not the period it describes |
| Look-ahead in fundamentals | `fundamentals.get` shows each figure from the day after its filing date; restatements appear only from their own filing |
| Model memory of real prices | Disguise price-bar recordings, their news and fundamentals together and use the memorisation flags; ITCH recordings cannot be disguised |
| Revised macro data | ALFRED vintages: each value is visible only from the release that published it |
| Selection over many trials | Agent entries count their saved revisions as trials; give `trials` for others and read `deflated_sharpe` and PBO |
| Tuning on the test period | Split recordings with `split_recording()` and evaluate only test windows |
| Changing model versions | Each reply records the model version that served it; mixed versions in an entry are warned |
| Market impact of large orders | Approximated by `impact_bps`; the historical book itself does not react |
| Survivorship | Choose symbols with `members_on()` and check them with `survivorship_check()` against a point-in-time membership file |
| Repeated runs on one recording | Measure a method's own randomness, not market uncertainty; compare several recordings |

## References

- D. H. Bailey and M. López de Prado, "The Sharpe Ratio Efficient Frontier",
  *Journal of Risk* 15(2), 2012.
- D. H. Bailey and M. López de Prado, "The Deflated Sharpe Ratio",
  *Journal of Portfolio Management* 40(5), 2014.
- D. H. Bailey, J. M. Borwein, M. López de Prado and Q. J. Zhu, "The Probability of
  Backtest Overfitting", *Journal of Computational Finance* 20(4), 2017.
- M. López de Prado, *Advances in Financial Machine Learning*, Wiley, 2018.

[Section index](README.md) · [All documentation](../README.md)
