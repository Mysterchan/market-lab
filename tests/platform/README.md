# Platform tests

For the current behavior-to-test map, commands, CI matrix and optional Desktop setup, read
[verification](../../docs/development/verification.md).

```bash
make test-platform
# Focus one existing module:
python -m unittest discover -s tests/platform -p test_agent_tools.py -v
```

`make test-platform` also builds native components. Plain unittest discovery assumes required
native artifacts and Python dependencies already exist. Optional Desktop tests skip unless
enabled explicitly; see the verification guide before claiming whole-App acceptance.

## Reading tests

- Accounts/ledger/ORM: independent financial expectations, ownership and migration rollback.
- Prepared market/integration: indexed visibility, event sequencing and shared experiment setup.
- Agent lifecycle/interpreter/environments: real child cleanup, installed SDK and selected Python.
- Agent carrier/control/tools/trading: factory/context, work completion, retries, validation,
  reservations, fills and faults.
- Prepared Replay/replay boundaries: original operations, isolated destination and zero model reruns.
- Desktop suites: user commands, preparation/cancellation and optional rendering checks.
- Source release: exact-commit export, reproducibility, preview/sync ownership, local-change protection and write-failure recovery.
- Module/structure tests: selected dependency and package contracts, not a complete design review.

`test_agent_pipeline.py` verifies the shared prepared execution kernel with controlled
time and test drivers. Reusable JSON inputs live under [tests/fixtures](../fixtures/README.md);
compatibility cases there are separate from the current public SDK/tool interface.

Tests use temporary synthetic data. Preserve clear resource ownership and failure cleanup.
Cross-OS lifecycle and offline environment-preparation checks do not establish full native/Desktop
acceptance. Fault tests may deliberately kill a child; unexpected production exceptions
must still be treated as real faults.

Shared data and resource fixtures live in [support/](support/README.md). They define no
collected tests. Each suite owns its cleanup; a suite must not instantiate another
TestCase to borrow its setup or import test modules to obtain synthetic input.
