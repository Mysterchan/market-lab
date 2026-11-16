# ITCH decoding interfaces

[event.hpp](event.hpp) defines ITCH event and payload values.
[decoder.hpp](decoder.hpp) declares expected_length() and decode(). Unknown types or
wrong lengths fail; recognized messages that do not affect the book return nullopt.

Supply one complete payload, without a BinaryFILE length prefix or MoldUDP64 header.
The caller supplies its source frame number. Decoding neither owns a book nor manages
network recovery. These event types describe ITCH, not arbitrary market vendors.

[Implementation](../../../../src/feed/itch/README.md) ·
[Input contract](../../../../docs/reference/itch-input.md)
