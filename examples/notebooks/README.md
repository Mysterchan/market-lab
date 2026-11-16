# Example notebooks

Each notebook builds its own synthetic recording and temporary workspace, and uses
`RehearsalProvider` for the Agent, so it runs offline and costs nothing. Install the
package (`pip install market-lab-research` or `pip install -e .`) and Jupyter, then open one.

| Notebook | Shows |
| --- | --- |
| [01 Agent vs rule vs neural network](01-agent-vs-rule-vs-neural-network.ipynb) | A rehearsed Agent, a holding rule and a small neural network in a model file, evaluated on one window with intervals and deflated Sharpe ratios |
| [02 Evaluation and review](02-evaluation-and-review.ipynb) | Decision timeline, PnL attribution, lineage verification, run diff and a model risk report |
| [03 Custom execution model](03-custom-execution-model.ipynb) | One rule under frictionless, queue, latency-and-impact and strict execution, with costs compared |
| [04 Sweep and heat map](04-sweep-and-heat-map.ipynb) | A grid of rule settings, its results table and heat map |

`MARKET_LAB_NOTEBOOK_TESTS=1 python -m unittest discover -s tests/platform -p test_notebooks.py`
runs every notebook from top to bottom (about six minutes).

Replace `RehearsalProvider` with a real model to study an Agent; see
[local models](../../docs/user-guide/research-controls.md).
