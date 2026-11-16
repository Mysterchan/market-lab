# MoldUDP64 reception and recovery

[moldudp.cpp](moldudp.cpp) validates a full datagram: 20-byte header, message lengths,
heartbeat/end markers, session and sequence range. [live.cpp](live.cpp) owns the socket,
transport and ordered publication. Within that file, ReorderBuffer owns the bounded
reorder queue, duplicate evidence, sequence/end boundaries and recovery deadlines.
Separate helpers receive datagrams, send recovery requests and publish ordered frames.
Only successful publication acknowledges a buffered frame.

The receiver starts with an empty book at sequence 1. It buffers messages after a gap
until the full observed range is available. Input errors, conflicting duplicates,
timeouts or resource limits fail the session. The App publishes a final CSV only after
success. The live Result type is currently shared with replay.

Current support is finite local IPv4 unicast simulation using POSIX sockets. Real
Nasdaq connectivity, multicast, SoupBinTCP, network checkpoints, snapshot bootstrap and
automatic session switching are not implemented.

Build with `make all`, then run:

```bash
python tests/test_live.py build/itch_replay
python tools/network_demo.py --executable build/itch_replay
```

Tests need local socket permission. They cover gaps, reorder, duplicates, partial/lost
recovery, malformed input, deadlines and limits.

[Headers](../../../include/itch/feed/moldudp/README.md) · [Native map](../../README.md)
