# Architecture

Start with the experiment flow. It introduces the components before the detailed process,
data and shutdown rules.

| Read in this order | What it explains |
| --- | --- |
| [Experiment flow](experiments.md) | Platform setup, saved run, Play and the decision/trading path |
| [Processes and threads](processes.md) | Which process executes each part and how tool requests travel |
| [Market data and ordering](market-data.md) | Prepared data, visible cutoffs, financial positions and recorded operations |
| [Lifetime and shutdown](lifecycle.md) | Resource owners, Pause, Stop, failure and close |
| [Native market engine](native-engine.md) | C++ decoding/book reconstruction versus simulated account execution |
| [Several symbols in one experiment](multi-symbol-design.md) | Accepted design: merged timeline, account, risk, execution and Replay |
| [Evaluating Agents against baselines](agent-evaluation-design.md) | Accepted design: shared metrics, repeated runs, statistics and disguised data |

To find where to change code, use the [code map](../development/code-map.md).
For user controls rather than implementation, read [execution modes](../user-guide/execution-modes.md).

[All documentation](../README.md)
