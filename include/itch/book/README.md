# Visible order book

[book.hpp](book.hpp) declares Book, BookState, LiveOrder, Level and Snapshot.
The book tracks one symbol's visible market orders and aggregates shares by price.
It is shared by file and network input; it is not the simulated account's order system.

Supply events in order. Depth limits the output snapshot, not the stored book.
Book has no internal thread synchronization. Discard an instance after apply fails.
State restoration validates the replacement before changing the current state.

[Implementation and tests](../../../src/book/README.md)
