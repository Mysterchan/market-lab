# Agent Python runtimes

This package selects and prepares an interpreter. It is not the experiment Runtime
process in `experiments/scheduling`, and it does not execute an Agent loop.

- `versions.py`: supported CPython requests and matching policy; default 3.12.
- `manager.py`: reuse, cancellable preparation, native probe and readiness publication.
- `provider.py`: invoke a trusted uv helper with private installation/cache directories.
- `probe.py`: inspect a trusted interpreter using isolated standard-library code.

Platform injects `persistence/agents/runtimes.py` for filesystem operations. Shared
job/subprocess lifecycle lives in `agents/preparation`; dependencies belong in
`agents/environments`. There is no extra experiment process or SQL table.

See [runtime selection and examples](../../../docs/agent-development/python.md).
