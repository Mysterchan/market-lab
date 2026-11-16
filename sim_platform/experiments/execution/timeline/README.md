# Order market events and commands

`Sequencer` fixes receipt time and queue order under one lock. Its claim watermark
prevents a later request from being inserted into a time range already being processed.
`ExecutionTimeline` merges market events and commands on the single financial owner.
Equal-time market events come first. Financial work uses the injected transaction path.

Prepared ordinals count retained market events. Raw source frame numbers are separate;
use `reader.source_sequence()` to trace the original input. The financial cursor decodes
an event once. Reading the time index does not rebuild a book.

Pause freezes the clock and admission at one cutoff. Stop invalidates commands that have
not begun. Natural end instead drains accepted work up to the configured end boundary.
A stable book span can be skipped only after execution proves current orders cannot fill.

This package does not create Platform or import application, persistence, Agents or Qt.
Its owner is [ExperimentSession](../session.py). Read the [experiment flow](../../../../docs/architecture/experiments.md)
and [timeline tests](../../../../tests/platform/test_prepared_market.py) before changing ordering.
