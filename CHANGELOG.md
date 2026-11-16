# Changelog

## 1.0.0 — 2026-11-16

First stable release: the public API is frozen ([API stability](docs/reference/api-stability.md)),
installers and wheels are built for macOS, Windows and Linux, and CI runs on all three.

- Agents on several symbols read the experiment's news with `news.list`, point in time.

- Borrow on a short still open at the end of a run is accrued in the run's costs
  (`pnl_attribution()['costs']['borrow_accrued']`); the cash ledger still charges borrow
  at cover.

- Versioned API reference generated from docstrings with Sphinx (`tools/build_api_reference.py`),
  built into the website under `api/<version>/` by `tools/build_pages.py`.
- Four example notebooks (`examples/notebooks/`): Agent vs rule vs neural network,
  evaluation and review, execution models, sweep and heat map; CI runs them.
- [Troubleshooting](docs/user-guide/troubleshooting.md), [governance](GOVERNANCE.md),
  `CODEOWNERS` and `CITATION.cff`; pull requests are now welcome.
- Release pipeline: on a version tag the release workflow checks tag, version and
  changelog, publishes to PyPI by trusted publishing and creates a GitHub Release with
  installers, checksums and provenance attestations, each behind maintainer approval.

- Agent sandbox: the experiment setting `sandbox` (`kind` auto, macos, bubblewrap or
  podman; `network` none, local or model) runs an imported Agent's process under
  `sandbox-exec`, bubblewrap or a rootless Podman container with writes confined to its
  work folder. Agent SDK 0.1.5.
- CI: Windows (MSVC native build, platform and Desktop tests, wheel demo) and Linux
  sandbox jobs; pip-audit, CodeQL and a CycloneDX SBOM (`security.yml`).
- Public API: [API stability](docs/reference/api-stability.md) defines the public API,
  semver and deprecation; `tools/check_public_api.py` freezes it in
  `tests/fixtures/public-api.json` and CI fails on an unrecorded change.

- Local models: the `openai-compatible` provider talks to a Chat Completions server on
  this machine or a private network (vLLM, Ollama, llama.cpp, LM Studio) at
  `ModelProfile.base_url`, with no key needed; public hosts are refused so data stays on
  premises. The Desktop's Agent editor offers it as **Local server**.
- Model budgets: the Agent setting `budget` (`max_tokens`, and/or `max_usd` with
  `prices`) ends a run cleanly, status `stopped` and reason `budget`, before a model
  request once the budget is spent; evaluations report `budget_exceeded`.
- Experiment lineage: every run records, write once, its code version and commit, rule
  and native-library digests, configuration digest and the SHA-256 of every input file;
  its records are sealed when it ends and an Agent's log, model and served versions when
  the log is archived. `Platform.run_lineage()` reads it and `Platform.verify_run()`
  proves a stored run unchanged or lists what changed.
- `Platform.decision_timeline()` links each Agent order to the reads, model replies and
  journal notes of its round; `Platform.pnl_attribution()` reports alpha, beta to the
  traded market, optional factor exposures and commission/borrow/impact costs.
- `Platform.model_risk_report()` exports one run's or a study's assumptions, data
  digests, model, tests and limits as JSON or Markdown; `Platform.diff_runs()` lists the
  settings, prompt, model, data, code and results that differ between two runs.
- `Platform.run_sweep()` runs a grid of parameters or Agent prompts (each prompt saved
  as a revision) on every recording, with a results table, deflated Sharpe ratios that
  count every combination as a trial, and `research.sweeps` heat maps (SVG).
- Headless parity: `market-lab` commands `agents`, `agent-import`, `credential`,
  `experiment --config`, `evaluate-runs`, `suite`, `sweep`, `disguise`, `split`,
  `lineage`, `verify`, `timeline`, `attribution`, `diff` and `risk-report` cover every
  Desktop research workflow (`research.headless` runs one experiment from JSON).
- Offline mode (`MARKET_LAB_OFFLINE=1`) refuses the hosted provider and every download;
  an acceptance test runs a whole study while auditing socket connections.
- `research.forward.ForwardTest` forward-tests a method in shadow mode on arriving bars:
  one run per batch on one account, with an append-only arrival log.
- `research.portfolio`: equal, inverse-volatility, risk-parity and minimum-variance
  weights, volatility targeting, exposure limits, share targets and rebalance orders.
- The Desktop's saved Agent result lists each order with the reads, model replies and
  notes of its round (**Agent decisions**).
- Wheels include the native library (`setup.py`, `tools/native_build.py`), tagged
  `py3-none-<platform>`; `pip install .` compiles it when a C++17 compiler is present.
  `tools/build_desktop.py` builds a macOS `.dmg` (signed and notarised when the
  maintainer's identity is set), a Windows Inno Setup installer and a Linux AppImage;
  `.github/workflows/release.yml` builds wheels (cibuildwheel), the sdist and the three
  installers as workflow artifacts. The frozen App now ships the execution-rule sources
  that Replay identifies, so preparing a market works in the bundle.

- The package installs with `pip install .` and provides a `market-lab` command; the
  native library is found through `MARKET_LAB_NATIVE_LIBRARY`, the package or `build/`.
- Diagnostics use Python logging: `--log-level` and `--log-json` on the command line,
  `MARKET_LAB_LOG_LEVEL` / `MARKET_LAB_LOG_FORMAT`, or `diagnostics.configure_logging()`.
  Handled failures are logged with their traceback; nothing prints by default.
- `Platform` methods are grouped into records, Agent and credential modules; the public
  API is unchanged.
- Execution models: `displayed-limit-queue-v1` fills resting orders after the displayed
  shares ahead of them trade; `impact_bps` adds square-root market impact (fills record
  `quoted_price`); `latency_jitter_ns` adds a per-order delay fixed by the order ID.
- Agent tools `documents.search` and `documents.read` over filings and reports
  (`documents_path`); `convert_news()` and `convert_documents()` import vendor exports at
  their publication times; `fundamentals_path` also reads ALFRED macro vintages and
  folders of files; the `DataConnector` protocol connects a vendor through the same
  files. `disguise_recording()` disguises news and fundamentals with the prices.
  `research.universe` finds survivorship bias in a symbol choice. `research.frames`
  exports results as tables, pandas DataFrames, CSV or Parquet, and Parquet bars
  convert to recordings (optional extras `parquet` and `dataframes`).
- Execution settings `latency_sigma_milli` (lognormal latency with a heavy right tail)
  and `borrow_bps_per_year` (borrow cost charged when a short is covered, recorded as
  `borrow_fee`).
- Evaluations count an Agent entry's saved revisions as its trials when none are given
  (`trial_sources`), list the model versions the provider served (`served_models`) and
  warn when an entry mixes them. `Platform.split_recording()` writes walk-forward
  train/test windows, and `ResearchBatch.evaluate_benchmark()` runs a reproducible
  five-regime benchmark against fixed baselines.
- Evaluations report the probabilistic and deflated Sharpe ratio per entry (`trials`
  says how many configurations were tried) and the probability of backtest overfitting
  across entries. `research.overfitting` adds walk-forward and purged k-fold splits.
- Agent tools `news.list` and `fundamentals.get`. Fundamentals come from a CSV or an SEC
  EDGAR companyfacts file (`fundamentals_path`) and are point in time: each figure and
  each restatement is visible only from its publication date. An Agent may now be
  granted, and offer its model, up to 32 tools (was 16).
- Several symbols for Agents (`symbols` in the Agent settings; every market tool takes any
  of them), for Live manual trading and for the rule and hold methods (one rule per
  symbol). Live and Agent runs schedule against a merged timeline, and Replay rebuilds
  every symbol's market.
- Reference Agents: `paper` and `strict` execution presets, model usage per role
  (`TradingAgent` `label`, `by_label`), a `journal.write` tool read with
  `Platform.agent_journal()`, beliefs carried into a later run, and `RehearsalProvider`
  to rehearse any Agent without model calls. Reference Agents accept Python 3.9 to 3.14.
  The Desktop fills a chosen reference Agent's name.
- Reference Agents after TradingAgents, FinMem and FinCon, selectable in the Desktop's
  New Agent form or with `Platform.import_reference_agent()`. `TradingAgent` accepts
  `finish_tools` so a role can end its turn with a local tool.
- `ModelProfile.temperature` is sent to the provider and recorded with each Agent run.
- [Methodology](docs/reference/methodology.md) states fill assumptions, statistics and
  known biases.
- Contributor guide, issue and pull-request templates, Dependabot, a coverage job for
  Python 3.9–3.12 and a wheel install check in CI.

## 0.1.0 — release candidate, never published

First source release of the local Agent trading experiment platform.

- Desktop Agent library, Python source import, saved revisions and dependency preparation.
- Historical market preparation, simulated accounts and limit-order execution.
- Human/Rule policies and single-agent experiments through the common Platform API.
- Model-file policies: Controlled experiments driven by your own Python model, such as a
  neural network, returning target positions or orders.
- Price-bar CSV recordings (daily or intraday) with a synthetic book, alongside ITCH.
- Market orders with a protection price, and optional short selling with a margin rule.
- Manual and model-file experiments on several symbols from one account, in the Desktop
  and the Platform API.
- Agent evaluation: `Platform.evaluation()` compares saved runs of Agents and baselines
  with bootstrap intervals, permutation tests and Holm adjustment; `evaluate_suite()`
  repeats Rule and model-file runs over several recordings; `disguise_recording()`
  hides a price-bar file's dates and price level from a model's memory, and Agent runs
  are flagged when the model may have seen their dates.
- Result figures (Sharpe, drawdown, win rate, ...) move to `research.metrics`, with new
  total return, Sortino ratio and exposure.
- Desktop **Compare runs** page: choose finished runs grouped by Agent revision, rule,
  model file or manual trading, evaluate them against a baseline and export the report.
- Agent entries in `evaluate_suite()`: saved Agent revisions run in real time over their
  window, with rule and model-file baselines on the same window; `concurrency` plays
  several Agent runs at once.
- Agent model usage: each reply's token counts and each request's dispatch time are
  recorded in the operation log; evaluations report model calls, failures, tokens, reply
  time and, with `token_prices`, cost.
- Turn-based Agents (`speed='turn'`): the market waits while the Agent works and moves to
  its next turn when it is idle, measuring decisions without speed and running much
  faster than the window. Evaluations keep Live and turn-based runs apart;
  `latency_cost()` reports what thinking time cost. The Desktop's New experiment offers
  Live or turn-based pacing for a saved Agent, and the workspace badge shows it.
- Prepared market visibility and separately managed agent/tool/runtime processes.
- Workbench with light/dark appearance, account/order inspection and bounded agent activity.
- Original-operation financial Replay, with no repeated model calls.
- Public agent SDK, local custom tools and standalone-project examples.
- Apache-2.0 project license and source installation and security guides.
- SDK exports carry project and upstream notices; pre-license wheel caches are rebuilt.

Deferred: comparison rubric/leaderboards, multi-agent collaboration, long-term memory,
Desktop installers, automatic updates, real brokerage/exchange execution, and full
cross-OS Desktop acceptance.
