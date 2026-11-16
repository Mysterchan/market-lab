# Native input interfaces

| Folder | Boundary |
| --- | --- |
| [binaryfile](binaryfile/README.md) | Stream framing to one complete payload |
| [itch](itch/README.md) | One payload to a validated event or an explicitly skipped message |
| [moldudp](moldudp/README.md) | Datagram/session/sequence validation and bounded network reception |

ITCH describes message content. MoldUDP64 describes how messages travel. File input
uses the same ITCH decoder without the network receiver. Event types remain ITCH-specific;
transport validation does not establish source authentication.

[Implementations](../../../src/feed/README.md)
