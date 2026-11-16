# Develop the platform

Build and run the source using the [project README](../../README.md), then read the
[experiment flow](../architecture/experiments.md). Platform is the public composition
entry; a change should preserve its resource and execution ownership.

| Task | Guide |
| --- | --- |
| Find the component for a change | [Code map](code-map.md) |
| Follow code and interface conventions | [Coding guidelines](coding.md) |
| Run checks and understand CI coverage | [Verification](verification.md) |
| Change command-line registration/handlers | [CLI implementation](cli.md) |
| Change the Desktop renderer | [Desktop visual reference](desktop-design/README.md) |
| Update these guides | [Documentation policy](documentation.md) |
| Prepare a public source update | [Release procedure](releases.md) |
| Set up, check and submit a change | [Contributing](../../CONTRIBUTING.md) |
| Understand planned scope | [Roadmap](roadmap.md) |

Tests and benchmark inputs belong beside their consumers, not in the user documentation.
See [test fixtures](../../tests/fixtures/README.md) and [benchmark profile](../../tools/fixtures/README.md).
External pull requests are currently closed; see [project status](../../README.md#project-status).

[All documentation](../README.md)
