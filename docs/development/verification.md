# Verify a change

Tests check stated behavior; passing tests do not prove that a design is easy to maintain.
Review responsibility boundaries, data ownership and documentation as well as results.
Run commands from the repository root with the intended Python environment activated.

## Local checks

```bash
python -m pip install -r requirements-dev.txt
make test
make python-quality
python tools/check_documentation.py
```

`make test` builds native components, runs native replay/network suites, then discovers
`tests/platform/test_*.py`. Optional Desktop suites skip unless enabled. `python-quality`
runs Black and the configured basic Ruff rules; it does not perform a full type/design audit.
For a focused test, use `python -m unittest discover -s tests/platform -p test_NAME.py -v`.

To measure coverage of the platform package (CI reports it for Python 3.9–3.12):

```bash
python -m coverage run -m unittest discover -s tests/platform -p 'test_*.py'
python -m coverage report
```

Diagnostics use Python logging under the `sim_platform` logger and print nothing by default.
Add `--log-level debug` (and `--log-json` for one JSON object per line) before the CLI
subcommand, or set `MARKET_LAB_LOG_LEVEL` and `MARKET_LAB_LOG_FORMAT`. From Python, call
`sim_platform.diagnostics.configure_logging()`. Handled failures are logged with their traceback.

```bash
python -m pip install -r requirements-desktop.txt
make test-desktop
```

Qt/WebEngine may need a usable graphics/platform environment. Offscreen tests supplement,
but do not replace, a native window/rendering check on the OS you intend to support.
The Ubuntu CI job uses Xvfb's virtual display with `QT_QPA_PLATFORM=xcb`, Mesa software
graphics, `QT_QUICK_BACKEND=software` and Chromium `--disable-gpu`. This avoids relying
on the offscreen plugin to provide an OpenGL/Vulkan context on a GPU-less runner.
Before starting Qt, the job also checks the xcb plugin and WebEngine process with
`ldd`. Missing shared libraries then produce an explicit dependency failure rather
than a generic platform-plugin message or crash during the test suite.
`make test-desktop` respects an explicit `QT_QPA_PLATFORM`; its local default is offscreen.
It prints each test name and enables Python's fault handler, including in the isolated
WebEngine startup process, so a native crash retains the active test and Python stacks.
To reproduce the CI display setup on Linux with the required Xvfb/Mesa packages installed:

```bash
QT_QPA_PLATFORM=xcb QT_QUICK_BACKEND=software LIBGL_ALWAYS_SOFTWARE=1 \
QTWEBENGINE_CHROMIUM_FLAGS=--disable-gpu \
xvfb-run --auto-servernum --server-args="-screen 0 1440x1000x24" make test-desktop
```

The application, Desktop fixtures and capture tools use `desktop.app.create_application()`
so shared OpenGL contexts are configured before QApplication is constructed. An isolated
startup test imports WebEngine afterward and verifies real HTML/JavaScript rendering.
Closed Desktop windows delete their widget/WebEngine tree after the worker stops.
The test fixture explicitly delivers deferred deletion events because it drives Qt
with `processEvents()` rather than the application's main event loop; repeated-open
coverage checks that the old browser and page are destroyed before reopening.
Dataset catalog updates cross from the worker into a GUI-owned `QObject` slot.
The Desktop suite checks that delivery with a real `QThread`; a plain Python
callback would run in the emitting worker and must never mutate widgets.
`make format-check` needs clang-format compatible with `.clang-format`; it is a native
format check, not currently part of the source CI job.

## CMake acceptance

```bash
cmake -S . -B build -DPython3_EXECUTABLE="$(which python)"
cmake --build build --parallel 2
ctest --test-dir build --output-on-failure
```

CTest and Make both run native replay, native networking and full platform discovery.
Use `-B build` for acceptance: App fixtures resolve native outputs under the source `build/`
directory. Other CMake output directories are not an accepted platform-test configuration.
Do not run Make and CMake against the same build directory concurrently.

## CI checks

| Workflow | Configured checks | Does not establish |
| --- | --- | --- |
| Source build and tests | Python 3.12: Ubuntu 24.04 / Make and macOS / CMake; exact-commit archive extraction, dev dependencies, Black/Ruff, native and whole-platform tests, CLI commands | Native Desktop rendering, full Windows App, every Python/OS combination |
| Desktop | Ubuntu 24.04 / Python 3.12: enabled Desktop suites under Xvfb/xcb with software graphics, including isolated real-WebEngine startup | Native window/rendering acceptance on every OS |
| Agent process lifecycle | Ubuntu/macOS/Windows / Python 3.9 and 3.12: lifecycle, installed SDK/selected interpreter, cross-process file locks/workspace cleanup, Windows project-capture adapter, official offline environment preparation/reuse/failure cleanup | Full native Windows App or OS rendering |

These are configured checks, not claims of remote acceptance before the new workflows run.
Inspect actual job results and skip reasons. Windows environment preparation runs the
real preparation suite rather than only a layout fixture. Full Windows source/GUI acceptance
is still not claimed. See [support boundaries](../user-guide/support.md).

Branch protection/required reviews are separate GitHub settings. See the
[public source update procedure](releases.md) for commit selection, previews and merge review.

## Behavior-to-test map

| Behavior | Start with these files under `tests/platform/` |
| --- | --- |
| Source import, revisions, dependencies, selected interpreter | `test_agent_projects.py`, `test_agent_project_capture.py`, `test_agents.py`, `test_agent_environments.py`, `test_python_runtimes.py`, `test_agent_interpreter.py` |
| Shared setup, modes and time visibility | `test_experiment_controls.py`, `test_live_experiments.py`, `test_prepared_market.py`, `test_agent_pipeline.py` |
| Price-bar files and multi-day recordings, including an Agent across the closed market | `test_bar_source.py`, `test_multi_day_agent.py` |
| The model method: bars, target-order rule, model process and its environment | `test_model_policy.py` |
| Price-bar spread/depth options, dated custom starts and `replay_run()` on prepared data | `test_bar_options.py` |
| Result-page statistics: round trips on the ledger's cost basis, Sharpe, turnover | `test_result_statistics.py` |
| Market orders, short selling and its margin rule, in every method and Replay | `test_orders.py` |
| Several symbols: merged timeline, per-symbol books, model targets, statistics and Replay | `test_multi_symbol.py` |
| Single-symbol results identical to the code before several symbols (golden outputs) | `test_single_symbol_unchanged.py` |
| Evaluation statistics, comparability, memorisation flags, disguised recordings and a real suite | `test_evaluation.py` |
| Agent factory, context, loop and tools | `test_agent_sdk.py`, `test_agent_carrier.py`, `test_agent_tools.py`, `test_agent_tool_transport.py` |
| Scheduling, wait, timeout and retry | `test_experiment_runtime.py`, `test_agent_control.py`, `test_models.py` |
| Reservations, fills, cancellation and transaction failure | `test_execution.py`, `test_agent_trading.py`, `test_ledger.py`, `test_core_boundaries.py` |
| Child shutdown and interrupted cleanup | `test_agent_lifecycle.py`, `test_agent_process.py`, `test_async_agent_reads.py` |
| Recorded operations without model reruns | `test_prepared_replay.py`, `test_replay_boundaries.py` |
| Imports, persistence and migrations | `test_module_boundaries.py`, `test_orm.py`, `test_resource_framework.py` |
| Desktop commands and rendering | `test_desktop*.py` |
| Shared colors, active text contrast and primary-button hover states | `test_design.py` |

This is a navigation map, not a claim that every rule is exhaustively covered. Fault tests
may deliberately crash a child; specific startup/clock failure tests run in isolated captures and check the expected
traceback plus their normal assertions. Unexpected subprocess failures include captured diagnostics. Inspect the test outcome and failure scenario;
an identical exception during normal App use is a fault, not something to suppress.

## Performance and real data

Use the [tool index](../../tools/README.md) to choose a current benchmark. Keep workload,
source revision, OS/Python/native versions, cold/warm setup and thresholds with results.
Do not add percentile values across overlapping phases or turn synthetic timings into a
hard-real-time guarantee. Real-data tests require separately supplied permitted data;
source CI uses redistributable synthetic fixtures and scripted providers, not paid API calls.


## Known Windows gaps

CI runs the whole platform and Desktop suites on Windows. Two Agent tests are skipped there
(`test_orders` market order and `test_prepared_replay` retry-then-trade): with the
scripted test model the Agent's order never arrives on Windows runners, and the cause is
not yet found. The same paths pass on macOS and Linux. Batch-suite tests on Windows also
tolerate the workspace database still being open when their temporary folder is removed.

[Section index](README.md) · [All documentation](../README.md)
