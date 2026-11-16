# Capture and format read results

`capture.py` fixes the data and cutoff for a `CapturedRead`. Immutable market data can
be captured without the financial queue; mutable account/order data uses its owner.
`ReadFormatter` in `formatter.py` formats that detached capture in the Tools process.

- `formats.py` converts domain fields to tool results.
- `history.py` selects history rows.
- `paging.py` keeps pagination tied to the original cutoff.

Formatting does not query SQL, read history files, call back into a live market or
submit trades. See the [tool flow](../README.md) for validation and ownership.
