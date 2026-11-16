# Roadmap

This page separates the implemented source edition from future work. It is not a release
date commitment. [Current capabilities](../user-guide/support.md) and [verification](verification.md)
state what exists and what has actually been checked.

## Available now

The source edition includes local Human/Rule/Agent experiments, saved Agent projects,
Python environment preparation, desktop workflows, original-operation Replay and
[Agent evaluation](../user-guide/evaluation.md) against baselines from Python.
External pull requests are not currently accepted; see the repository's
[project status](../../README.md#project-status).

## Deferred work

| Direction | Boundary |
| --- | --- |
| Desktop installers, signing and updates | Source installation comes first; packaged distribution needs separate acceptance |
| Additional OS coverage | Full application/environment/rendering verification, beyond limited process/environment tests |
| Conversation management | Provider context limits exist; automatic summary/memory is not currently supplied |
| Richer trading/data models | Leverage, news merged across several symbols, and additional source adapters need explicit contracts |
| Multi-agent experiments | Single Agent remains the present focus |
| Strong uploaded-code isolation | Trusted local code is the current execution model; process separation alone is not an OS sandbox |

Planned work is not a promise of delivery dates or implementation of every proposed feature.

[Section index](README.md) · [All documentation](../README.md)
