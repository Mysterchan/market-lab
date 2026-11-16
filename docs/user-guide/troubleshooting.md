# Troubleshooting

Find the message or symptom, then the fix. For anything else, run the command again with
`--log-level debug` (or call `sim_platform.diagnostics.configure_logging('debug')` in
Python): handled failures then log their full traceback. Never paste API keys into an issue.

## Installing and starting

| Symptom | Cause and fix |
| --- | --- |
| `native market library not built` while installing | No C++17 compiler was found, so the wheel is pure Python. Install the platform wheel (`py3-none-<platform>`), or a compiler (Xcode command-line tools, build-essential, Visual Studio Build Tools) and reinstall |
| `OSError` loading `libitch_market` | The native library is missing or for another architecture. Run `make all` in a checkout, or set `MARKET_LAB_NATIVE_LIBRARY` to the library's path |
| macOS says the App "cannot be opened" | A local build is ad-hoc signed. Right-click the App and choose Open, or use a signed and notarised release |
| Windows SmartScreen warns about the installer | Beta installers are not yet signed; choose More info, then Run anyway, after checking `SHA256SUMS` |
| The AppImage does not start | Run `chmod +x` on it; on older distributions install `libfuse2` |
| The Desktop window is blank on Linux | Install the Qt runtime libraries (`libxcb-cursor0`, `libxkbcommon-x11-0`, `libegl1`) |

## Data and markets

| Symptom | Cause and fix |
| --- | --- |
| `Times must be distinct` or a CSV is refused | See [price-bar input](../reference/price-bars.md): one row per bar, a date column, OHLCV columns |
| `replay source file changed since the run` | The recording changed after the run. Restore the original file; `verify_run()` shows which input differs |
| `replay execution implementation mismatch` | The run was recorded with other execution rules (an older version). Replay it with the version in its lineage (`run_lineage()['inputs']['code']`) |
| The first experiment on a large ITCH file starts slowly | Its SHA-256 is computed once for the run's lineage and cached; later runs start at once |
| `runs are not comparable` in an evaluation | The runs differ in a setting named in the message (cash, costs, window, symbol). Rerun them with equal settings |

## Agents and models

| Symptom | Cause and fix |
| --- | --- |
| `AUTH_FAILED` | The model key is wrong or missing. Store it again under **Settings → Model credentials** or with `market-lab credential` |
| `RATE_LIMIT` or `PROVIDER_TEMPORARY` repeated, then `RETRY_EXHAUSTED` | The provider refused requests. Raise `retry.max_attempts`, lower concurrency, or use a local model |
| `base_url host must be localhost or a private IP address` | A local model profile must point at this machine or a private network; for a hosted service use the `openai-responses` provider |
| A local model's replies end the run with `FORMAT_ERROR` | The model did not return valid tool calls. Use a model with tool-calling support (vLLM `--enable-auto-tool-choice`, Ollama tool models) and rehearse first |
| The run stopped with reason `budget` | Its model budget was spent; see `model_usage['budget_exceeded']` and raise `budget` if intended |
| `AGENT_DEPENDENCY_MISSING` | The Agent imports a package its environment lacks. Add it to the project's requirements and prepare the environment again |
| `Agent environment is not ready` | Prepare it: **Agents → Prepare**, or `prepare_agent_environment()` |
| `... needs the network, but offline mode is on` | `MARKET_LAB_OFFLINE` is set. Use a local model server, a wheelhouse or an offline Python archive, or unset it |
| `no Agent sandbox is available on this machine` | Install bubblewrap or Podman (Linux), or remove `sandbox` from the settings; macOS uses `sandbox-exec`, which is built in |
| An Agent run looks stuck in Live mode | Live market time keeps moving while the model thinks. Use turn-based pacing (`speed='turn'`) to measure decisions without speed |

## Results

| Symptom | Cause and fix |
| --- | --- |
| No equity curve on a result | The run had no archived bars, or started with positions; figures that need the curve are left empty |
| A Sharpe ratio is marked unreliable | Fewer than 20 daily returns; use a longer window or more recordings |
| `verify_run()` reports `code_changed` | The code now running differs from the run's; the stored run is unchanged, but Replay may disagree |
| Results differ between two runs | `diff_runs()` lists every setting, prompt, model version, input and code difference |

[Section index](README.md) · [All documentation](../README.md)
