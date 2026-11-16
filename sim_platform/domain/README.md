# Domain values and account rules

- `models.py`: identities, accounts, decision makers, runs, events and read-only snapshots.
- `json_values.py`: validation, copying and encoding of finite JSON values.
- `agents.py`: AgentRevision and PreparedAgentExperiment.
- [ledger/](ledger/README.md): apply financial events to account state.

This layer does not import application, persistence, CLI or SQLAlchemy. Keep database
sessions, UI output and market matching outside it.

[Package map](../README.md)
