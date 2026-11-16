# Agent tool objects

`Tool` describes local computation: name, description, parameters and typed `run()`.
It needs no client and returns any finite JSON value. `PlatformTool` adds a client
and proxies operations over IPC; market/account/trading/control classes inherit it.
`collection.py` contains only built-in platform proxies. User tools belong in the
user's Agent project and must never be added to this registry or the App formatter.

`invoke()` checks call signatures, delegates domain validation to the tool, and
validates JSON results. Server protocol checks still enforce grants, argument
bounds, work validity and financial rules. Neither model schemas nor SDK objects
replace those checks. Uncertain transport errors propagate without trade retry.
See the [public SDK and examples](../../../sdk/README.md).
