# Desktop walkthrough

The current Desktop keeps data management, trading controls and Agent editing
inside the main window. The video predates this navigation change and shows some
of them as separate windows; use the [illustrated guide](desktop-walkthrough-zh-hk.md)
for current screen layouts and return actions.

The short tutorial shows the actual Market Lab Desktop with synthetic market data
and temporary accounts. Its captions are visible in the video; a separate English
caption track is also available. No model/provider is called and no API key is used.

[Watch or download the tutorial](../site/media/demo.mp4)

## 1. Prepare an experiment

Start the demo after installing the source edition:

```bash
python -m sim_platform desktop --demo
```

Open **New experiment**. Choose the market recording, simulated account, decision
maker and start point. **Preview market data** is optional. **Start experiment**
prepares the data automatically and opens the experiment paused. Press Play or Step
to advance it; Controlled mode lets you advance one market event at a time.

## 2. Submit and inspect a simulated order

With a Human experiment open, use **Controls & records** to enter a limit order.
The tutorial submits a buy order against the synthetic AAPL book. Stepping forward
shows the accepted order fill through the real execution engine. Inspect the order,
position and cash together, then stop the experiment before starting another one.

The synthetic dataset is deliberately small. This walkthrough explains behavior,
not strategy quality or investment performance.

## 3. Find the Agent editor

Open **Agents** to create a built-in template or import a Python strategy/carrier.
The tutorial opens the editor to show this entry point; it does not run a model-backed
Agent. Saved changes create an immutable revision for future experiments.

For a real Agent experiment, choose its model and granted tools, set the matching
credential reference, and prepare an imported project's dependencies. Follow the
[Agent project guide](../agent-development/projects.md) and [SDK guide](../agent-development/sdk.md).

## 4. Run a Live baseline

The tutorial next creates a Live experiment using the built-in Buy → Hold → Sell
rule. Market time advances at 1× while decisions run. This rule is an understandable
baseline, not an AI model. Agent experiments also use background Live execution.

Compare [Controlled and Live modes](execution-modes.md) before selecting your own run.

## 5. Verify and review a recorded run

After the Live rule run ends, open its operation log in **Replay** and select
**Verify & open**. Verification checks the original financial events using a separate
account. The recording checks that the replay matched, events were verified and
model calls stayed at zero. Play, Next action and seek then change the viewing
position without executing the decision maker again.

See [first use](getting-started.md) for data, account and credential setup, and
[support and limitations](support.md) for the V0.1 scope.

[User guide](README.md) · [All documentation](../README.md)
