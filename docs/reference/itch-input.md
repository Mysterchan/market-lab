# Accepted ITCH / BinaryFILE input

This is the input accepted by [decoder.cpp](../../src/feed/itch/decoder.cpp), not a complete
implementation of every ITCH field. For exchange field definitions, see the
[Nasdaq ITCH specification](https://classic.nasdaqtrader.com/content/technicalsupport/specifications/dataproducts/NQTVITCHSpecification.pdf).
The native market book is separate from the experiment account's simulated orders.

## File boundaries

Input is one uncompressed BinaryFILE session. Each message starts with a two-byte big-endian
payload length, followed by that many bytes. A zero-length record ends the file; bytes after
that marker are rejected. Records are processed in file order, not resorted by timestamps.
See [BinaryFILE framing](https://nasdaqtrader.com/content/technicalSupport/specifications/dataproducts/binaryfile.pdf).

By default the ending marker is required. `--allow-implicit-eof` accepts only immediate EOF
after a complete locate-zero `S/C` End-of-Messages event. A partial length or payload still
fails. Validate a compressed source separately before decompression; neither gzip validation
nor a valid ending proves that no events were lost in the middle.

## Message handling

Lengths below are ITCH payload bytes, excluding the two-byte BinaryFILE prefix.

| Type | Bytes | Current behavior |
| --- | ---: | --- |
| `R` | 39 | Read symbol/locate mapping; no CSV row |
| `A` | 36 | Add a target-symbol order and emit a book snapshot |
| `F` | 40 | Same book behavior as A; extra attribution fields are not used |
| `E` | 31 | Reduce an order by executed shares |
| `C` | 36 | Reduce displayed quantity at the original order price |
| `X` | 23 | Cancel part of an order |
| `D` | 19 | Delete an order |
| `U` | 35 | Replace an order with a new ID, quantity and price |
| `S` | 12 | Validate type/length, otherwise skip |
| `H`, `Y`, `L` | 25, 20, 26 | Validate type/length, otherwise skip |
| `V`, `W`, `K`, `J` | 35, 12, 28, 35 | Validate type/length, otherwise skip |
| `h` | 21 | Validate type/length, otherwise skip; lowercase is distinct |
| `P`, `Q`, `B` | 44, 40, 19 | Validate type/length, otherwise skip |
| `I`, `N`, `O` | 50, 20, 48 | Validate type/length, otherwise skip |
| Other types | — | Reject as unknown |

For skipped types, only the type and total length are checked. Other fields do not update
the book or emit CSV; this does not mean they lack trading significance. Non-target-symbol
orders are generally skipped. Conflicting symbol/locate mappings and target-symbol updates
referencing unknown orders are rejected. Native CSV snapshots are not the prepared trade/news
query format used by the Python application.

## Failure and output

Malformed framing, unknown types, wrong lengths, invalid endings and inconsistent target
book updates fail with a nonzero exit status. Diagnostics identify the failing/last accepted
frame and first untrusted byte offset. A frame is a local file-record count, not an exchange
transport sequence.

With `--output`, the utility writes a hidden temporary file and publishes only after the
requested replay and optional verification succeed. Normal failures preserve an existing
CSV. Successful publication replaces it. Input/output aliases are rejected. stdout can
already contain rows when a later event fails, so inspect exit status rather than assuming
that any output means completion.

Explicit stop produces a prefix whose unread tail and ending are not validated. Checkpoint
recovery has separate suffix/full-CSV modes and requires compatible source/options; read
[native replay and recovery](native-engine.md). Forced termination can leave temporary or
referenced journal files; this is not a general power-loss durability guarantee.

BinaryFILE lacks live transmission sequence numbers. The parser detects known format/book
contradictions, not every silent data loss. [Network input](network-input.md) describes the
separate local receive/recovery demonstration.

[Section index](README.md) · [All documentation](../README.md)
