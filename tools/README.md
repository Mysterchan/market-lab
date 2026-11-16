# Developer tools

Run tools from the repository root with the project Python environment. Build native
components first where required: `make all`. Use `--help` to inspect options.
Benchmarks are opt-in experiments; they are not ordinary regression tests or latency guarantees.

## Current tools

| Tool | Purpose / inputs |
| --- | --- |
| [build_source_release.py](build_source_release.py) | Export a specified Git commit; explicit `--working-tree` creates a labeled local candidate |
| [sync_public_source.py](sync_public_source.py) | Preview/apply an archive to a clean public checkout; never commits or pushes |
| [build_agent_sdk.py](build_agent_sdk.py) | Export the local SDK wheel for external Agent development |
| [check_documentation.py](check_documentation.py) | Check English guides/docstrings, local links and headings; add `--include-internal` for current internal documents |
| [capture_desktop.py](capture_desktop.py) | Native desktop captures using controlled display fixtures; requires Desktop dependencies |
| [benchmark_desktop_controls.py](benchmark_desktop_controls.py) | Measure desktop control behavior with a prepared fixture directory |
| [benchmark_prepared_import.py](benchmark_prepared_import.py) | Compare complete source preparation and optional CSV; supply permitted historical input |
| [benchmark_prepared_runtime.py](benchmark_prepared_runtime.py) | Fixed synthetic execution/Agent/Tools workload from the checked-in profile |
| [benchmark_prepared_real_window.py](benchmark_prepared_real_window.py) | Actual source peak-window measurement; supply permitted historical input |
| [benchmark_prepared_checkpoint.py](benchmark_prepared_checkpoint.py) | 35-second run spanning the real periodic financial checkpoint |
| [benchmark_experiment_runtime.py](benchmark_experiment_runtime.py) | Runtime scheduling and communication experiments |
| [benchmark_live_experiments.py](benchmark_live_experiments.py) | Live multi-participant load/failure experiments; writes under `build/benchmarks/` |
| [benchmark_platform.py](benchmark_platform.py) | Standalone native/market/execution/policy/Desktop component comparisons; not the whole prepared Agent path |
| [probe_agent_runtimes.py](probe_agent_runtimes.py) | Opt-in interpreter provisioning probe; downloads require explicit flags |
| [replay_platform_run.py](replay_platform_run.py) | Compatible raw-source recorded-run Replay into an empty destination; not the prepared-operation Replay API |
| [network_demo.py](network_demo.py), [network_sender.py](network_sender.py) | Finite local UDP feed/recovery simulation and offline comparison |
| [validate_contract_fixtures.py](validate_contract_fixtures.py) | Bounded compatibility-fixture checks, not current-runtime certification |
| [agent_probe_records.py](agent_probe_records.py) | Compact measurement helper, not a standalone product command |

## Lightweight entrypoints

```bash
python tools/build_agent_sdk.py --help
python tools/benchmark_prepared_import.py --help
python tools/benchmark_prepared_runtime.py --help
python tools/replay_platform_run.py --help
python tools/validate_contract_fixtures.py
```

Historical benchmark names no longer present in this folder are not aliases for the current
tools. Do not reuse an old command with a renamed executable or compare results across
changed workloads as though they were the same experiment.

## Running measurements

```bash
python tools/benchmark_prepared_runtime.py --include-agent --output build/runtime-study.json
python tools/benchmark_prepared_import.py --source /absolute/path/feed.bin --output build/import-study
python tools/benchmark_prepared_checkpoint.py --output build/checkpoint-study.json
```

The runtime command measures policy cases by default. Add `--include-agent` to also
measure model IO, Agent CPU parsing and complete Tools IPC. It does not switch architectures.
The import command defaults to a limited prefix; add `--full-day` only for an intentional
full-source experiment. Inputs and output-path requirements are tool-specific. Use a new,
dedicated destination and inspect `--help` before running a costly measurement.

The fixed [profile](fixtures/prepared-runtime.json) is consumed directly
by the runtime/checkpoint/real-window scripts. Preserve workload and thresholds with results.
Fresh outputs should normally go under ignored `build/`; the current Live probe's default
docs output is a legacy location, not approval to publish a new internal report.

For a local network demo use the [network guide](../docs/reference/network-input.md).
For test commands, CI scope, optional Desktop setup and measurement limitations see
[verification](../docs/development/verification.md). Source publication is a separate reviewed
action; generating a source archive does not push, tag or publish anything.

Source export and sync: [release procedure](../docs/development/releases.md).

## Website and tutorial

`build_pages.py` renders the existing public Markdown into the GitHub Pages site,
builds search and checks local URLs/anchors. Install `requirements-site.txt` first.
`record_desktop_demo.py` captures a real synthetic-data tutorial through Desktop controls;
its optional encoder is pinned in `requirements-demo.txt`. It verifies a fill and a
recorded Live run, uses a temporary workspace and makes no model calls.

See [website setup and recording](../docs/site/README.md) for local preview and deployment.
