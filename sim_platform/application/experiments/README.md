# Platform-owned experiment composition

Platform remains the sole composition root; this package exports no second facade.

- contracts.py: typed setup services, run binding, source-launch lifetime and detached capture values; no IPC or extra runtime owner.
- agent.py: assemble Agent execution, Tools, Runtime and program host from explicit
  capabilities; owns rollback until returning the runner, receives no Platform.
- factory.py: compose Live Human/Rule runner, shared prepared execution lifecycle,
  independent Runtime, optional trusted Rule program and rollback of owned children.
- runtime.py: validate Agent scheduling inputs and assemble the trusted Runtime child.
- pipeline.py: assemble the prepared financial Session, replay seed and terminal evidence.
  Both receive explicit capabilities; Platform retains root registration and shutdown.
- binding.py: authoritative Runtime admission, committed financial observations,
  retained-journal resync and completion; no scheduler, model worker or SQL.
- agent_driver.py: detached market/account capture, original financial command
  capability and ordinary JSON output/archive. Immutable market reads bypass the
  financial owner; account/state reads use its receipt barrier.
- runtime_policy.py: trusted adapter injected into the Runtime child; opens only
  the selected immutable artifact and observes clock-visible market changes.
- pacer.py: TurnPacer, the thread that advances a turn-based Agent's clock between its
  turns once the owner, the Runtime and the Agent are settled
  ([turn-based Agents](../../../docs/user-guide/evaluation.md#turn-based-agents)).
- replay.py: finite original-operation Replay using recorded receipt positions,
  original Commands/Coordinator/Service and a matching destination account; no Agent/model.
  Validation, command translation and financial comparisons live in [replay](../../replay/README.md).

Controller owns runner/market; runner owns lifecycle/host/log/Tools/Runtime.
Execution owner alone modifies the financial state. Output owns formatting waits,
JSON and post-run archive. Preparation and inventory are separate capabilities.
Shared execution and scheduling mechanisms live in [experiments](../../experiments/README.md).
This application package supplies composition and trusted adapters; it does not own
a second execution loop.

[Verification and support scope](../../../docs/development/verification.md).
