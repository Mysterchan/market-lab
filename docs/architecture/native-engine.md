# Native market engine

The C++ engine reads historical ITCH messages and maintains the market's public order book.
The Python financial engine separately maintains the experiment account and its simulated
orders. A market-data Add message is not the user's trading request.

```text
BinaryFILE reader → ITCH decoder → native order book
                                 ├─ standalone replay → CSV/checkpoint output
                                 └─ bridge/preparation → immutable prepared market data
```

| Change | Source / interface |
| --- | --- |
| Framing and file boundaries | [reader](../../src/feed/binaryfile/reader.cpp), [input contract](../reference/itch-input.md) |
| Message decoding | [decoder](../../src/feed/itch/decoder.cpp), [headers](../../include/itch/feed/itch/README.md) |
| Order book updates/invariants | [book](../../src/book/book.cpp), [book interface](../../include/itch/book/README.md) |
| Python/native prepared bridge | [bridge](../../src/bridge/README.md), [prepared market](../../sim_platform/market/prepared/README.md) |
| Standalone CLI/output | [application](../../src/app/README.md), [CLI guide](../reference/native-engine.md) |
| Checkpoint/recovery | [reference](../reference/native-engine.md) |
| Simulated network input | [reference](../reference/network-input.md) |

Prices and quantities use bounded integer values. The native decoder rejects malformed
frames and unsupported message structures rather than guessing. The book tracks individual
orders and aggregated price levels. Display depth is separate from complete internal book
state. Python financial fills use their documented visible-depth simulation model; they
are not exchange queue-position certification.

Build with `make all`; source checks are described in [verification](../development/verification.md).
The native tests include a separate Python reference model for synthetic events. Whole-day
or sample measurements from older revisions remain historical evidence, not a new validation
of the current source. The [source map](../../src/README.md) lists component entrypoints.

[Section index](README.md) · [All documentation](../README.md)
