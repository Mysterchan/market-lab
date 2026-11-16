# Account ledger

| File | Responsibility |
| --- | --- |
| `state.py` | In-memory account, intent, fill and update-result values |
| `actions.py` | Open accounts, accept/reject orders and calculate fills and costs |
| `reducer.py` | Validate event history, select an action and advance its sequence |

A fill is validated and fully calculated before the account changes. The updated result
is validated before its history advances. A rejected event leaves account state unchanged.
This package depends on domain values, not ORM or market matching.

[Domain map](../README.md)
