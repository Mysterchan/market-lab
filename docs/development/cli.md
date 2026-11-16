# Change the command-line interface

The CLI translates command-line arguments into calls on Platform-owned resources.
User command examples belong in the [command-line guide](../user-guide/command-line.md).
Use subcommand `--help` for the complete current argument list.

## Registration and execution

```text
python -m sim_platform <command>
  → cli/main.py builds the parser
  → cli/commands registrations declare commands, arguments and handlers
  → parse_args chooses the handler
  → handler receives args and CliContext
  → context opens Platform; handler calls the public operation and formats output
```

`add_parser()` declares a command. `add_argument()` declares its inputs.
`set_defaults(handler=...)` stores a function reference; it does not execute it.
`CliContext` resolves the selected workspace and owns Platform access. Registration must
not load market data, create accounts or start experiments.

| Change | Source |
| --- | --- |
| Root parser/dispatch | [main.py](../../sim_platform/cli/main.py) |
| Registration list | [commands/__init__.py](../../sim_platform/cli/commands/__init__.py) |
| Workspace/resource access | [context.py](../../sim_platform/cli/context.py) |
| Evaluate/compare arguments and output | [commands/research.py](../../sim_platform/cli/commands/research.py) |
| Batch preparation/evaluation | [research/batch.py](../../sim_platform/research/batch.py) |
| Reports/export | [research/reports.py](../../sim_platform/research/reports.py) |

The batch adapter requests a controller from Platform rather than instantiating its own
root. It prepares source, creates a fresh account/run, starts Controlled policy execution,
pumps until terminal state, reads a report and closes in `finally`. Prepared market data is
used; this is not a second raw playback architecture. Comparison invokes independent runs.

Keep parsing, resource composition, business behavior and output separate. CLI USD inputs
are converted to integer money before entering the core. Do not overwrite market/news inputs
with an export. Failed report status must produce a failed exit status even when a JSON
report is still emitted. Preserve `--data-dir` behavior, including comparison persistence.

Run [CLI tests](../../tests/platform/test_cli.py), applicable batch/research tests and
[verification](verification.md). Review user help/examples whenever arguments change.

[Section index](README.md) · [All documentation](../README.md)
