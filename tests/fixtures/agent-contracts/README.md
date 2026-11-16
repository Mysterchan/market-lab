# Agent compatibility fixtures

These inputs retain earlier contract shapes for regression checks. They are not the current
Agent SDK specification and are not loaded as a platform configuration.

| Input | Scope |
| --- | --- |
| `wire-examples.json` | Selected saved-Agent cases in `tests/platform/test_agents.py` |
| `scheduling-cases.json` | Independent scheduling expectations in `tests/platform/test_experiment_runtime.py` |
| `contracts.schema.json`, `semantic-cases.json` and wire examples | Bounded schema/semantic checker |

Run `python tools/validate_contract_fixtures.py` from the repository root for the checker.
It uses only the schema keywords present here; it is not a general JSON Schema validator
or a runtime/OS/provider acceptance suite. See [verification](../../../docs/development/verification.md)
for current test coverage.
