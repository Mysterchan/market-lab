# App-side platform tool routing

`router.py` contains AgentTools: schema/grant validation, immutable read capture,
owner-side routing for financial/control operations, and submission of captured
values to the Tools child. `result_history.py` tracks bounded in-memory results.
Neither file is a child process entrypoint.

The Agent runner performs Runtime admission and chooses submit_read or ordered
submit. Financial effects remain on the execution owner; expensive result formatting
is delegated through [worker/host.py](../worker/host.py).

`ToolResultHistory.capture()` shares original-result lookup, reservation and error retention across immutable and financial paths. Unknown financial failures retain their reservation; immutable capture may release it. Routing categories come from the Tool classes, and account/state reads remain on the financial owner.
