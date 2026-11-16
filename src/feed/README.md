# Market input formats and transports

| Folder | Input and output |
| --- | --- |
| [binaryfile](binaryfile/README.md) | Local stream -> validated frame payload |
| [itch](itch/README.md) | ITCH payload -> validated event or recognized skip |
| [moldudp](moldudp/README.md) | UDP datagram -> ordered messages with bounded recovery |

File replay and the Python bridge share the BinaryFILE reader and ITCH decoder.
MoldUDP64 adds session, sequence and transport framing around the same messages.
A datagram may carry several payloads.

Add a transport beside these modules rather than inside Book. A source with different
event meanings needs an explicit adapter contract. Current network acceptance covers
finite local unicast simulation; it does not authenticate a source or verify real
Nasdaq, multicast or TCP connectivity.

[Headers](../../include/itch/feed/README.md) · [Network guide](../../docs/reference/network-input.md)
