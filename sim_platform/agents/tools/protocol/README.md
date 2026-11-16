# Tool validation and transport values

| File | Checks or result |
| --- | --- |
| `contracts.py` | Request fields, run/work identity, grants, argument types and ranges |
| `responses.py` | Result identity, captured position, named errors and UTF-8 byte limits |
| `transport.py` | Bounded worker frames and the allowed detached value types |
| `errors.py` | ToolError codes shared by callers and handlers |

Names, argument schemas and categories come from the [Tool classes](../client/README.md).
A valid transport frame does not grant authority to trade. Financial validation stays
in execution. Worker frames contain captured values, never a live reader, ORM session,
callback or credential. Read the [complete tool flow](../README.md) before adding a tool.
