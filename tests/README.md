# Tests and verification

Run from the repository root with the intended Python environment:

```bash
python -m pip install -r requirements-dev.txt
make test
make python-quality
```

`make test` builds the native executable/shared library, runs the replay and local network
suites, then discovers all platform test modules. The [verification guide](../docs/development/verification.md)
explains optional Desktop tests, CI scope and known gaps. CTest currently enumerates a subset
of platform modules and is not equivalent to whole-platform Make discovery.

| Location | Scope |
| --- | --- |
| [test_replay.py](test_replay.py) | Synthetic ITCH framing, independent book expectations, output and native recovery |
| [test_live.py](test_live.py) | Finite loopback UDP ordering, recovery and output comparison |
| [platform/](platform/README.md) | Accounts, persistence, market, experiments, Agent/SDK/processes, Replay and optional Desktop |
| [verify_real_sample.py](verify_real_sample.py) | Opt-in historical-input event checks and sampled independent book recomputation |
| [verify_checkpoint_day.py](verify_checkpoint_day.py) | Opt-in whole-day interruption/recovery comparison |
| `bench_*.py`, `bench_*.cpp` | Native performance research; inspect input/platform assumptions before use |

Ordinary automated tests use synthetic fixtures and temporary storage; they do not require
paid model calls or redistributed exchange recordings. Local socket/process/Qt tests require
corresponding OS permissions. A skipped optional suite is not a passed suite.

Choose independent expectations and test the meaningful failure boundary. Use controllable
clocks for ordering/timeouts; do not assume a slow CI worker completes setup before a short
real-time source ends. Keep performance thresholds in deliberate workload experiments.
Fixture helpers should own cleanup without manually calling another TestCase's setUp.

When adding a behavior, update its test and the current guide. Test count alone does not
measure coverage or maintainability. Expected child-crash tests currently may print traceback
output; parent assertions and final results determine whether that failure was expected.
