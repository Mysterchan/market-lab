# Human and Rule decisions

- `runner.py`: PolicyDecisionRunner for caller-driven Controlled execution.
- `live/runner.py`: LivePolicyDecisionRunner for background Human/Rule execution.
- `live/owner.py`: short policy commands and observation capture on the App owner.
- `live/program.py`: TrustedPolicyProgram, run in the Rule child; Human has no automatic policy child.
- `policies.py`: built-in policies and the external JSON-policy adapter.
- `model_file.py`: the model method: bars, the target-order rule and the model process.
- `model_worker.py`: runs in the model's own interpreter; loads the user's file (stdlib only).
- `models.py`, `contracts.py`: observations, configurations and policy contracts.
- `controls.py`, `handoffs.py`, `scheduling.py`: manual control and policy authority changes.
- `invocation.py`: calling a policy without owning its financial transaction.

The Live runner coordinates shared experiment infrastructure. It is not the Agent
runner and does not contain the user Agent's model/tool loop. See
[decisions/agent](../agent/README.md) for that method's App-side integration.
