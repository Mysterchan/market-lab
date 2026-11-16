# Private beta guide

Thank you for testing Market Lab before its public release. This page takes you from
installation to a finished, reviewable study in about an hour, without help. Please
send us what was unclear, slow or wrong at each step.

## What we ask you to do

1. Install Market Lab and run the demo.
2. Run one study: an Agent (with your own model, or a local model) against a rule and
   buy and hold, on a recording you choose.
3. Review it: evaluation, decision timeline, PnL attribution and a model risk report.
4. Tell us where you got stuck, using the questions at the end.

## 1. Install

Use the installer for your system, from the link we sent you:

| System | File | First start |
| --- | --- | --- |
| macOS 12 or later (Apple Silicon) | `Market-Lab-<version>-macos-arm64.dmg` | Open the image and drag Market Lab to Applications |
| Windows 10 or later (64-bit) | `Market-Lab-<version>-windows-x86_64.exe` | Run it; it installs for your user only, without administrator rights |
| Linux (x86-64, glibc 2.35 or later) | `Market-Lab-<version>-linux-x86_64.AppImage` | `chmod +x` the file and run it |

Check the download against `SHA256SUMS`. During the beta the Windows installer is not
yet signed, so Windows may warn before running it. For scripting, install the Python
package instead: `pip install market-lab-research` gives the
`market-lab` command, with no C++ build needed.

Start the App. To try it on a small fixture recording first, run `market-lab desktop
--demo` (from the Python package), or `market-lab demo` for the command-line check,
which creates a temporary workspace, trades the fixture and removes it again.

## 2. Run a study

1. **Data → Import**: add a price-bar CSV ([format](../reference/price-bars.md)) or an
   ITCH file. To keep a model from recognising the period, disguise the recording
   first (`market-lab disguise`); see [evaluation](evaluation.md).
2. **Agents → New Agent**: start from a reference Agent or your own Python project.
   Under **Model provider** choose OpenAI (store your key under **Settings → Model credentials**)
   or **Local server** for vLLM, Ollama or llama.cpp on your machine, so no data leaves
   it ([research controls](research-controls.md)).
3. **New experiment**: choose the recording, the Agent and **Turn-based** pacing, which
   measures decisions without speed and runs faster than the market window.
4. Repeat for the baselines: a rule run and a buy-and-hold (hold) run on the same
   recording, cash and costs.

Before paying for a real model, rehearse the Agent: set its model name to `rehearsal`
in a script, or add `"rehearsal": "SYMBOL"` to a `market-lab experiment` configuration.
To cap spending, add a `budget` to the Agent settings.

## 3. Review it

- **Compare runs**: choose the Agent's runs and the baselines; read the intervals,
  p-values and the deflated Sharpe ratio, not only the means.
- **Results → Agent decisions**: each order with what the Agent read before it.
- From Python or the command line: `pnl_attribution` (alpha, beta, costs), `diff_runs`
  (why two runs differ) and `model_risk_report` (one reviewable document):

```bash
market-lab risk-report --profile PROFILE --runs AGENT_RUN RULE_RUN --output review.md
market-lab verify --profile PROFILE --run AGENT_RUN
```

[Study tools](study-tools.md) explains every figure. A study can also run entirely from
the command line; see [the command-line guide](command-line.md#run-a-whole-study-headless).

## Known limits during the beta

- Execution is simulated against recorded displayed liquidity; read the
  [methodology](../reference/methodology.md) before quoting results.
- Market Lab never sends real orders and has no broker connection.
- Windows and Linux installers are new; please report anything that differs from macOS.
- Team workspaces, roles and a shared database are not part of the beta.

## Feedback we need

1. Where did you first need help, and what did you try?
2. Which step took longest, and was the wait explained?
3. Did any result look wrong or impossible? Send the run's model risk report.
4. What would you need before trusting a result from Market Lab in your own work?
5. What is missing for your usual study?

Report problems with the run ID, the model risk report and, when the App failed, the
log from `market-lab --log-level debug ...`. Do not send API keys or licensed data.

[Section index](README.md) · [All documentation](../README.md)
