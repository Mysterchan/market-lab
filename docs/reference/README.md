# Reference

Use these pages when you already know what you want to do. For a first run, use the
[user guide](../user-guide/README.md); for component ownership, use [architecture](../architecture/README.md).

| Topic | Reference |
| --- | --- |
| Public Python composition API | [Platform](platform-api.md) |
| Standalone C++ replay utility | [Native engine usage](native-engine.md) |
| Accepted historical file framing and message types | [ITCH / BinaryFILE input](itch-input.md) |
| CSV files of OHLCV price bars and their synthetic book | [Price-bar input](price-bars.md) |
| Limit and market orders, short selling and its margin rule | [Orders](orders.md) |
| Fill assumptions, statistics and known biases | [Methodology](methodology.md) |
| Local UDP receive/recovery demonstration | [Network input](network-input.md) |
| What is public, versioning and deprecation | [API stability](api-stability.md) |

The native CLI and Python account simulation are different interfaces. A native input
order changes the public market book; it is not a request to trade the experiment account.

[All documentation](../README.md)
