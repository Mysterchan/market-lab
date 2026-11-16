# Simulated network input

The standalone native engine supports a finite MoldUDP64 unicast receive/recovery path.
The repository includes a loopback sender and an offline comparison demo. This is not a
live Nasdaq connection or broker integration.

```bash
make all
python tools/network_demo.py --executable build/itch_replay --output-dir build/network-demo
```

Use a dedicated output directory: the tool writes fixtures, output CSV and logs. It injects
missing/reordered messages, exercises recovery and compares received output with offline
replay. It uses real local sockets; your environment must permit loopback binding.

For sender options run `python tools/network_sender.py --help`. Native receive syntax is
validated in [options.cpp](../../src/app/options.cpp); the native executable does not
implement `--help`. The [engine guide](native-engine.md) describes native commands. [live.cpp](../../src/feed/moldudp/live.cpp) owns receive/recovery state;
[moldudp.cpp](../../src/feed/moldudp/moldudp.cpp) parses datagrams;
[test_live.py](../../tests/test_live.py) verifies finite local scenarios.

No multicast, production endpoint/authentication, multi-session service or network restart
acceptance is claimed. Successful loopback recovery does not prove loss-free real exchange
input or independent correctness of every historical book state.

[Section index](README.md) · [All documentation](../README.md)
