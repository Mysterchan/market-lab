# Regression inputs

These are data consumed by tests, not user configuration or development plans.

| Input | Consumer |
| --- | --- |
| `agent-tools.schema.json` | Tool envelope assertions in `tests/platform/test_agent_tools.py` |
| [agent-contracts](agent-contracts/README.md) | Saved-Agent and Runtime regression cases; bounded contract checker |
| `single_symbol_golden.json` | `tests/platform/test_single_symbol_unchanged.py`: outputs of single-symbol runs produced by the code before several symbols existed; regenerate only from such a commit |

Keep fixture changes paired with independent behavior expectations. Moving a fixture requires
updating its consumers. Do not describe compatibility fixtures as the complete current SDK
contract; public interfaces are documented in [Agent development](../../docs/agent-development/README.md).
