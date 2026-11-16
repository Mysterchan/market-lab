# Operations inside a transaction

These services apply business rules using a caller-owned unit of work. They do not
create Platform, commit transactions, access ORM rows or start background execution.

| File | Responsibility |
| --- | --- |
| `accounts.py` | Accounts and ledger operations |
| `makers.py` | Decision-maker registration |
| `runs.py` | Run state and intents |
| `queries.py` | Research queries |
| `agents.py` | Saved Agent revisions and experiment settings |
| `agent_runs.py` | Validate settings/account/source and create the Agent's maker and run together |
| `agent_logs.py` | Check log ownership and archive terminal records |

`ApplicationServices` assembles these services. SQL queries belong in
[persistence repositories](../../persistence/sql/repositories/README.md).
Process and market factories stay outside the transaction.
