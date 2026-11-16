# Current capabilities and support

This page describes the source edition and the scope of existing verification.
Configured CI jobs are not evidence that an unpushed change passed on another OS.

## Available workflows

- Desktop, CLI and Python access to local simulated accounts and historical market data.
- Controlled manual and model experiments across [several symbols](several-symbols.md)
  (Desktop or Platform API).
- Human and Rule experiments, Controlled experiments driven by your own
  [model file](model-policies.md), and saved Agent revisions using a built-in template or
  trusted imported Python source.
- Dependency preparation, selected Agent interpreters, installed SDK and reusable environments.
- Prepared historical data with clock-limited visibility; Agent and Live policy work can
  continue while the financial owner processes events and commands.
- Limit-order acceptance, cash/share reservation, partial fills, cancellation and account history.
- Model IO and local user tools in the Agent process; platform tool requests cross IPC.
- Recorded-operation Replay that recalculates financial outcomes without rerunning a model.
  `Platform.replay_run()` verifies finished Controlled runs, including runs on price-bar
  files, by preparing the original file again; Live and Agent runs use their operation log.
- Native ITCH decoding/order-book rebuilding and a separate loopback network-feed simulator.

The current architecture is documented [here](../architecture/experiments.md). The native
engine and the simulated account order book have different responsibilities; see
[native architecture](../architecture/native-engine.md).

## Support and validation matrix

| Area | Current evidence / limitation |
| --- | --- |
| macOS arm64 Desktop | Existing local acceptance on Python 3.9.6 / PySide6 6.8.3; rendering and demo were checked during source-release preparation |
| Ubuntu 24.04 / Python 3.12 | CI builds native components, runs source tests, formatting/lint and first-run CLI commands |
| macOS / Ubuntu / Windows, Python 3.9 and 3.12 | CI configured for lifecycle, installed SDK/interpreter and official offline environment preparation; new preparation jobs await actual remote results |
| Full Windows App | Not accepted; native build and Desktop acceptance still require Windows execution |
| Full Linux Desktop | Enabled offscreen suites are now configured in CI; actual remote results and native window/rendering acceptance remain pending |
| App Python | Source edition targets Python 3.9–3.12; the main source workflow covers 3.12, not every version on every OS |
| Agent Python | Provisioning policy accepts native CPython 3.9–3.14 requests; this is not whole-App or dependency certification on all those versions |
| Model providers | Built-in adapter exists; automated integration uses scripted/local providers, not paid live-provider acceptance |
| OS credential storage | Optional macOS keychain; session-only credentials remain available; no general non-macOS persistent-store claim |

See [how each check runs](../development/verification.md). Job configuration states what CI
is intended to run; inspect the actual run before claiming that a particular commit passed.

## Boundaries

This is a local simulation platform, with no broker or real exchange order submission.
Historical public depth is not hidden liquidity or a consolidated multi-venue book.
A recording made from price bars has a synthetic book built from assumptions. Multi-day
bar files work in Controlled and Live mode; Live mode skips the hours between sessions.
Trading uses simulated cash with limit and [market orders](../reference/orders.md); short
selling is off unless the experiment allows it, and then follows a fixed margin rule.
Several symbols are available to Manual, Rule and model-file experiments in Controlled
mode and to Manual and Agent experiments in Live mode; news is not merged across symbols. Leverage, multi-agent evaluation and an
overall agent leaderboard are outside this release.
[Agent evaluation](evaluation.md) compares saved runs with intervals and tests, in the
Desktop's Compare runs page or from Python; it computes no overall score.

Agent code is trusted local code unless its experiment sets an OS `sandbox`
([SDK guide](../agent-development/sdk.md)). Pause closes capability/delivery gates
and freezes experimental time; it does not suspend arbitrary user CPU instructions.
Provider context limits still apply. There is no automatic conversation summary/truncation.
There is no hard-real-time deadline guarantee. Native input, transport, scheduling, financial
processing and output all have costs; prepared data does not make those costs disappear.

Packaged Desktop installers, signing, automatic updates and full additional-OS acceptance are future
work. Historical data is not redistributed; the demo uses project-generated synthetic data.

[Section index](README.md) · [All documentation](../README.md)
