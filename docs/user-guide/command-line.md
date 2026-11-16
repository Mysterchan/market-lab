# Use the command line

Run commands from the built source checkout with its Python environment active.
For general commands, use `python -m sim_platform --help`; each subcommand has its own help.
The [standalone C++ utility](../reference/native-engine.md) is a separate interface.

```bash
python -m sim_platform --version
python -m sim_platform desktop --demo
python -m sim_platform evaluate --help
python -m sim_platform compare --help
```

## Evaluate a built-in rule

Supply your own permitted, uncompressed ITCH BinaryFILE:

```bash
python -m sim_platform --data-dir /absolute/path/research evaluate \
  --input /absolute/path/market.bin --symbol AAPL \
  --policy rule --quantity 10 --hold-ns 2000000000 \
  --cash 10000 --fee-per-share 0.01 \
  --output /absolute/path/evaluation.json
```

Evaluation prepares the market, creates a new profile/account/run, executes the policy and
returns its report. `--data-dir` precedes the subcommand and must be absolute. Without it,
evaluation uses the normal workspace. It does not continue an existing account's holdings.

| Option | Meaning / default |
| --- | --- |
| `--input` | Required local market file |
| `--symbol` | Target symbol; `AAPL` |
| `--news` | Optional local news JSON |
| `--allow-implicit-eof` | Accept only the compatible source ending described in the [input contract](../reference/itch-input.md) |
| `--library` | Override the native market library; normally resolved under `build/` |
| `--cash` | Opening USD cash; 10,000 |
| `--fee-per-share` | USD per filled share; 0.01 |
| `--latency-ns` | Simulated order eligibility delay; 0 |
| `--policy` | `rule`, `hold` or `model`; `rule` |
| `--allow-short` | Allow sales beyond the shares held, under the [margin rule](../reference/orders.md#margin-rule) |
| `--bar-spread`, `--book-depth` | Price-bar `.csv` input only: synthetic spread in USD (0.02) and levels per side (5); see [price bars](../reference/price-bars.md) |
| `--model-file`, `--model-parameters`, `--lookback-bars`, `--model-python`, `--model-timeout` | With `--policy model`: see [your own model](model-policies.md) |
| `--quantity` | BuyHoldSell buy quantity; 10 shares |
| `--hold-ns` | Its hold-time condition; 2,000,000,000 ns |
| `--output` | Optional JSON report or CSV event audit; parent directory must exist |

CLI money arguments are USD. Stored/report money uses integer units of USD 0.0001 and
reports `money_scale`. CSV exports are event audits, not a summary performance table.
`rule` is the fixed BuyHoldSell example. `hold` manages existing holdings without buying;
because this command starts with cash, it commonly makes no trades.

## Compare independent rule settings

Save `configurations.json`:

```json
[
  {"name": "Hold one second", "policy": "rule", "quantity": 10, "hold_ns": 1000000000},
  {"name": "Hold two seconds", "policy": "rule", "quantity": 10, "hold_ns": 2000000000}
]
```

```bash
python -m sim_platform compare --input /absolute/path/market.bin \
  --configurations configurations.json --output /absolute/path/comparison.json
```

The list accepts 1–20 configurations, each containing only `name`, `policy`, `quantity` and
`hold_ns`. Common market/news/cash/fee/latency options apply to every run. Without a file,
the default compares one-second and two-second holds. Runs execute sequentially with equal
starting cash in separate accounts; they do not inherit one another's state or perform handoff.
Comparison export is JSON only.

Default detailed comparison uses temporary storage unless you supply `--data-dir`.
Use `--summary-only` for compact output with persisted independent runs, or provide
`--data-dir` explicitly to keep the workspace. Keep `--output` if you want the returned
comparison saved as a file. An exception in one evaluation stops the comparison.

## Run a whole study headless

Every Desktop research workflow has a command, so a study can run from a script, a
scheduler or a server. Configurations are JSON files and every command prints JSON.

| Command | Does |
| --- | --- |
| `agents --profile ID` / `agents --reference-list` | List saved or reference Agents |
| `agent-import --profile ID --source PATH` (or `--definition FILE`, `--reference KEY`) | Save an Agent |
| `credential --reference NAME [--file .env --variable VAR]` | Store a model key in the OS store; without `--file`, one line is read from standard input |
| `experiment --config FILE` | Run one rule, model-file or Agent experiment to its end ([fields](../../sim_platform/research/headless.py)) |
| `evaluate-runs --profile ID --entry NAME=RUN,RUN ...` | Compare saved runs with intervals and tests |
| `suite --config FILE` | Run an evaluation suite (`recordings`, `entries` and suite options) |
| `sweep --config FILE [--heatmap X Y out.svg]` | Run a parameter or prompt grid (`recordings`, `base`, `grid`) |
| `disguise`, `split` | Disguise a price-bar recording; write walk-forward windows |
| `lineage`, `verify`, `timeline`, `attribution`, `diff`, `risk-report` | Review finished runs; `verify` exits 1 when a run changed |

An Agent experiment configuration:

```json
{
  "profile_name": "Research",
  "cash_usd": 100000,
  "source": "/data/aapl.csv",
  "method": "agent",
  "agent_id": "my-agent",
  "revision": 1,
  "settings": {"symbol": "AAPL", "speed": "turn", "start_ns": "34200000000000",
               "end_ns": "57600000000000", "budget": {"max_tokens": 200000}},
  "model": {"provider": "openai-compatible", "model": "qwen2.5:7b",
            "base_url": "http://127.0.0.1:11434/v1"}
}
```

Copy the remaining Agent settings (`retry`, `limits`, `lag` and the rest) from a saved
experiment or from `sim_platform.examples.configured_agent_demo.example_configuration()`.
Add `"rehearsal": "AAPL"` to rehearse the Agent without any model call. A hosted model
key is given as `"credential": {"reference": "key", "file": "/path/.env"}` or
`{"reference": "key", "environment": "OPENAI_API_KEY"}`; keys never appear in arguments.

## Work offline

Set `MARKET_LAB_OFFLINE=1` for air-gapped research. The hosted model provider, Python
runtime downloads and public package downloads are then refused with a clear error;
local model servers, wheelhouses and offline Python archives keep working, and nothing
else in Market Lab uses the network. The offline acceptance test runs a whole study
(rule and Agent runs, Replay, evaluation, lineage, report) while recording every socket
connection, and fails on any connection beyond loopback or the private network.

## Results and limits

Successful commands return exit code 0. Argument errors normally return 2; expected operation
errors and reports containing a failed run return 1. Inspect the report status as well as
stdout. A reported equity mark does not mean remaining shares were sold.

These commands run Controlled rule evaluation. A slow policy holds up the next simulated
event; its compute time is not automatically included in market time. The built-in policy
timeout is checked after return and cannot interrupt a stuck function. This route is not a
latency benchmark for live Agents, an exchange queue model or the planned Agent leaderboard.
For a saved custom Agent, use Desktop or the [Platform API](../reference/platform-api.md).

[Section index](README.md) · [All documentation](../README.md)
