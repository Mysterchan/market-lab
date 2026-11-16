# Tools process

- `host.py` runs in App. ToolProcessHost owns the bounded queue, sender/receiver
  threads, result Futures, gates and child cleanup.
- `process.py` runs in the Tools child. run_tools receives captured values, calls
  the read/financial formatter and returns a bounded result.

The child receives detached captured values, not App objects, database handles,
market readers or execution authority. It cannot decide whether an order fills.
Routing is in [server/router.py](../server/router.py); the Agent-facing proxies are
in [client](../client/README.md).
