# Decision runners

A DecisionRunner connects one decision method to an experiment's execution and
lifecycle. A runner is an App object, not a thread and not a process entrypoint.
Platform creates it; ResearchController owns the experiment's single runner.

- [base.py](base.py): common DecisionRunner lifecycle interface.
- [policy/](policy/README.md): Human/Rule observations, policies, Controlled runner,
  Live runner, policy invocation and authority handoff.
- [agent/](agent/README.md): AgentDecisionRunner connects the Agent host, platform
  tools and common execution pipeline.
- [service.py](service.py): shared atomic decision audit/state/action commit logic,
  used by the financial transaction path. It is not a scheduling loop.

```text
Human/Rule Controlled → policy/runner.py       → PolicyDecisionRunner
Human/Rule Live       → policy/live/runner.py  → LivePolicyDecisionRunner
Agent                → agent/runner.py        → AgentDecisionRunner
```

Background runners share [experiment execution](../experiments/execution/README.md)
and [work scheduling](../experiments/scheduling/README.md). Agent does not pass
through LivePolicyDecisionRunner. The Agent program runs in a child hosted by
[agents/hosting](../agents/hosting/README.md).

The package's existing public exports (for example `from sim_platform.decisions
import DecisionConfig, HumanPolicy`) remain available. Internal modules have one
canonical location; there are no forwarding files at the former paths.
