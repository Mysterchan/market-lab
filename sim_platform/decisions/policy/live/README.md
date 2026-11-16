# Live Human and Rule execution

This is the background policy runner, not the Agent runner.

- `runner.py`: LivePolicyDecisionRunner controls the policy-specific resources.
- `owner.py`: PolicyOwner captures observations and handles short manual/commit work
  on the App execution owner; it does not perform heavy policy calculation there.
- `program.py`: TrustedPolicyProgram runs Rule calculation in the decision child,
  using Runtime work and committing its response through the App owner.

Human manual commands have no automatic decision child. Platform injects the shared
host for Rule execution. The package imports no App composition, SQL, Agent host or Qt.
Both policy and Agent background execution share the infrastructure in
[experiments](../../../experiments/README.md). Agent uses
[decisions/agent](../../agent/README.md) instead of LivePolicyDecisionRunner.
