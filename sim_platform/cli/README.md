# Command-line adapters

This package translates user arguments into Platform-owned operations. It does not own a
second account engine or access ORM directly. [CLI development](../../docs/development/cli.md)
explains dispatch; [user commands](../../docs/user-guide/command-line.md) gives examples.

| Code | Responsibility |
| --- | --- |
| `main.py` | Root parser, handler dispatch and expected-error exit status |
| `commands/` | Feature-specific registration and handlers |
| `commands/__init__.py` | Explicit registration list and help ordering |
| `context.py` | Resolve workspace and open Platform lazily |
| `arguments.py`, `output.py` | Shared argument conversion and JSON output |
| `interactive.py` | Session command tables, arity validation and input loop |
| `market_queries.py` | Shared bounded market query commands/output |

Add a command in its feature module with `handler(args, context) -> int`, then register it.
Keep financial rules in the core. Expected operation errors return 1; argparse errors use 2;
unexpected programming failures retain diagnostics. `path` does not create storage and demo
workspaces are temporary by default. Use `python -m sim_platform --help` for current commands.
