# MoldUDP64 interfaces

[moldudp.hpp](moldudp.hpp) declares MoldDatagram and parse_moldudp64(). Its boundary
is the exclusive next sequence number. The parser validates a complete datagram.
[live.hpp](live.hpp) declares LiveOptions and the stateful receiver, with explicit
session/endpoints, timeouts and buffer limits. Its Result type comes from replay.

The receiver uses POSIX IPv4 sockets. Current acceptance covers finite local unicast
simulation, starting with an empty book at sequence 1. It does not establish real
Nasdaq connectivity, multicast, SoupBinTCP, snapshot bootstrap or network checkpoints.
Messages after a gap stay buffered until the observed range is complete.

[Implementation and tests](../../../../src/feed/moldudp/README.md)
