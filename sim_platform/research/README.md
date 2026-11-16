# Preparing and controlling experiments

ResearchController is the App object returned by Platform. It holds the selected
source, preparation status and experiment runner. It is not a process or thread.

- `controller.py`: common preparation, lifecycle and inspection interface.
- `agent_controller.py`: saved Agent choices and Agent experiment preparation.
- `policy_controller.py`: Human/Rule setup and policy-specific controls.
- `configuration.py`, `session.py`, `views.py`: configuration and detached view helpers.
- `datasets.py`, `jobs.py`, `batch.py`: data selection, background preparation and batch work.
- `queries.py`, `reports.py`: result queries and exports through injected capabilities.
- `metrics.py`: per-run figures (Sharpe, Sortino, drawdown, return, exposure, round trips).
- `evaluation.py`: comparing saved runs: comparability, bootstrap intervals, tests and
  memorisation flags ([user guide](../../docs/user-guide/evaluation.md)).
- `attribution.py`, `pnl_attribution.py`, `run_diff.py`, `model_risk.py`, `sweeps.py`:
  decision timelines, alpha/beta/cost attribution, run diffs, model risk reports and
  parameter or prompt sweeps ([study tools](../../docs/user-guide/study-tools.md)).
- `headless.py`: one experiment from a JSON configuration, for the command line.
- `forward.py`: forward tests in shadow mode on arriving bars.
- `portfolio.py`: weights, risk scaling, limits, share targets and rebalance orders.

## Setup

`Platform.open_research(method=...)` creates an empty controller. The caller can then
prepare data and call create_experiment(). `Platform.open_experiment(...)` follows
that same path in one call and returns a ready controller. No separate backend is
selected by choosing one of those two entry methods.

The Agent controller prepares/reuses immutable market data, saves configuration and
creates a run through injected Platform factories. Assembly creates its runner and
owned children. The Agent object is constructed in its child before readiness; the
clock has not started yet. Configure the model before Play.

Agent creation reuses an untouched prepared source at the requested start when its file
and publication identities still match. Repositioning the selected source retains its
news and implicit-EOF options. Selecting another recording starts with that recording's
options. All source positioning uses the common preparation path; synchronous callers
can wait_for_preparation(), while UI callers poll prepare_tick().

## Execution

The controller calls the runner's lifecycle API. Controlled policy work uses
[PolicyDecisionRunner](../decisions/policy/runner.py); Live Human/Rule uses
[LivePolicyDecisionRunner](../decisions/policy/live/runner.py); Agent uses
[AgentDecisionRunner](../decisions/agent/runner.py).
Background event ownership, output and scheduling live in
[experiments](../experiments/README.md). The controller does not implement another
market loop or create Platform. Platform registers the controller as the root;
the controller owns its runner and market, and closes them in the required order.

For full code/process navigation, begin at [the package map](../README.md).
