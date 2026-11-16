# First use from source

For a screenshot-by-screenshot explanation in Cantonese, use the
[Desktop illustrated guide](desktop-walkthrough-zh-hk.md).

These steps target the V0.1 macOS source edition. Run commands from the repository
root after following the README's Python environment and native build instructions.
Linux/Windows whole-App acceptance is not implied by Agent-only CI.

## Explore without credentials or historical data

```bash
source .venv/bin/activate
python -m sim_platform desktop --demo
```

The demo creates a temporary workspace and a small synthetic feed. It has no model
credential and sends no model requests by itself. Use New experiment to choose the
demo account and a Human/Rule decision maker. Prepare, then Play or Step in Controlled
mode. In the workbench, open **Controls & records** for manual order entry,
policy controls and audit tables. Stop before starting another active experiment. The synthetic recording is
small; reaching its end is expected, not a live feed outage. Closing removes demo
accounts and logs. Use the persistent app for work you want to keep.

## Keep a workspace

```bash
python -m sim_platform path
python -m sim_platform desktop
```

The first command prints the default location; it does not create a database.
The default macOS directory is `~/Library/Application Support/MarketLab/`.
An explicit directory must be absolute:

```bash
python -m sim_platform --data-dir /absolute/path/my-research desktop
```

Use the same path on later launches. Back up the whole workspace while the app is
closed. Reinstalling source is not a reason to delete account storage. Reset in
Settings is destructive; read its confirmation before using it.

## Use your own data

Market data → Add file accepts a local, uncompressed ITCH BinaryFILE, or a CSV file of
OHLCV price bars covering one or many days. A CSV file has no order book, so one is built
from stated assumptions; read [price-bar input](../reference/price-bars.md) first. You can keep
an external file reference or copy it into managed storage. Select the intended
symbol and start point in New experiment. Preparation processes the source before
trading can start; allow time and disk space for prepared artifacts. Cancellation
and failures are shown before the experiment is adopted as ready.

The repository does not ship historical Nasdaq recordings. Compressed `.gz` input
must be decompressed first. Some recordings lack the explicit BinaryFILE end marker;
only enable the additional implicit-EOF option for a compatible source. See the
[input contract](../reference/itch-input.md) and [engine guide](../reference/native-engine.md).

## Save and run an Agent

- Settings: set a model credential reference and key for the built-in model adapter.
  Session-only storage is the default; the OS credential store is optional where supported.
- Agents → Create Agent: choose template, Python strategy, or a Python carrier with
  its own loop. Imported code must export `create_agent(context)`; see the [SDK](../../sim_platform/sdk/README.md).
- Select the platform tools the agent may call. Local math/analysis tools belong in
  the agent's Python project, not in the platform tool server.
- Save. A later edit creates a revision; older experiments keep their existing revision.
- For an imported project, prepare the environment. Enable downloads only when you
  want them; offline wheel and Python archive inputs are under environment options.
- New experiment: choose Saved Agent, its exact revision, account and market. Agent
  experiments use Live 1×. Prepare, wait for Ready, then press Play.

A source import is not a sandbox or anti-cheating guarantee. Python, dependencies
and model providers are part of your trust boundary. An API request may cost money;
long conversations remain subject to the provider context window. Automatic memory
summarisation and multi-agent orchestration are not included in V0.1.

## Inspect and replay

The workbench displays committed account state, visible market data and bounded
recent agent activity. Expand recorded activity to inspect original results. Missing
market marks display as unavailable instead of invented profit.

After stopping/completing a Live rule or Agent experiment, choose Replay → Browse and select the run's
`.jsonl` operation log. The default layout is `<workspace>/agent-logs/<run-id>.jsonl`;
if your run used an explicit log location, use that file instead. Verification uses a
separate destination account; the original account/run is preserved. Once verified,
Play/Pause/Next action/seek affect viewing only, without another Agent or model call.
Original UI pause/resume/stop timings are not reenacted.
Controlled manual experiments do not currently produce the original-operation log
required by this Replay workflow; their saved results remain available in Experiments.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| `No module named PySide6` | Activate the intended environment and install `requirements-desktop.txt`. |
| Native library cannot open | Run `make all` from the repository root with a C++17 compiler. Do not copy native libraries between architectures. |
| No model credential/model configured | Set the matching credential reference in Settings and model identifier in the saved Agent. |
| Python environment cannot prepare | Check download consent, selected Python requirements, package errors and optional offline inputs. |
| Market source moved/changed | Relocate the same data or add it as a new dataset; do not reuse a stale prepared artifact. |
| No financial events in Replay | A verified experiment can finish without placing a trade. Inspect its final verification message. |

For a public bug report, include OS/architecture, Python version, `--version`, the
command/steps and a redacted error. Exclude API keys, credentials and proprietary
recordings. See [security reporting](../../SECURITY.md).

[Section index](README.md) · [All documentation](../README.md)
