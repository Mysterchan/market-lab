# Public API and versioning

From version 1.0, Market Lab follows [Semantic Versioning](https://semver.org/):
`MAJOR.MINOR.PATCH`. This page defines what the public API is, what may change in each
kind of release and how a feature is retired.

## What is public

| Public | Where |
| --- | --- |
| `Platform` and every method without a leading underscore | `sim_platform.application.platform`, imported as `from sim_platform import Platform` |
| The Agent SDK | `sim_platform.sdk` (also the separate `sim_platform_agent_sdk` wheel, versioned on its own) |
| Model settings and providers | `ModelProfile`, `ProviderError`, `ModelProvider`, `RehearsalProvider`, `OpenAICompatibleChat` |
| Execution settings | `sim_platform.execution.ExecutionConfig` and its fields |
| Research helpers | `research.attribution`, `pnl_attribution`, `sweeps`, `forward`, `portfolio`, `overfitting`, `metrics`, `universe` |
| Data connectors | `sim_platform.market.connectors` |
| Diagnostics and offline mode | `sim_platform.diagnostics.configure_logging`, `sim_platform.network` |
| Command line | `market-lab` command names, their arguments and their JSON output fields |
| Files | Price-bar CSV, news, fundamentals and documents formats; experiment, suite and sweep JSON configurations; report formats named by their `format` field |
| Saved data | Workspaces created by an earlier 1.x release open in every later 1.x release |

Everything else is internal, including modules not listed here, names with a leading
underscore, the Desktop's frontend and bridge, the native library's C interface, and
database tables. Internal code may change in any release.

`tools/check_public_api.py` records the names and signatures of the public objects in
`tests/fixtures/public-api.json`, and CI fails when they change without the record
being updated in the same reviewed change.

## What each release may change

| Release | May | May not |
| --- | --- | --- |
| Patch (1.0.x) | Fix bugs, improve documentation and performance | Change any public name, signature, output field or file format |
| Minor (1.x.0) | Add public names, optional arguments, output fields and file-format fields; deprecate | Remove or change existing public behaviour |
| Major (x.0.0) | Remove deprecated names; change behaviour, with migration notes | Remove anything that was not deprecated for at least one minor release |

A change that alters a simulated result for the same inputs (fills, fees, metrics) is a
behaviour change: it is announced in the changelog, and runs recorded before it keep
replaying with the rules recorded in their lineage or are refused, never silently
recalculated.

## Deprecation

1. The replacement ships first, in a minor release.
2. The old name keeps working and emits a `DeprecationWarning` naming its replacement
   and the earliest version that may remove it.
3. The changelog lists it under **Deprecated**, and the API reference marks it.
4. It is removed no earlier than the next major release.

## Before 1.0

Releases 0.x make no compatibility promise: any minor release may change the API. The
changelog lists every change.

[Section index](README.md) · [All documentation](../README.md)
