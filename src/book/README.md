# Visible market book

[book.cpp](book.cpp) applies adds, reductions, deletes and replacements; maintains
price-level totals; checks invariants; and exports/restores validated state.
It depends on ITCH event types, not sockets, files or CSV output.

`Book::apply()` selects the event path, then optionally verifies the book and returns
a snapshot. `apply_directory()` checks symbol/locate identity; `add_order()` validates
and inserts a selected symbol's order; `change_order()` reduces, deletes or replaces
an existing selected order. These helpers share the same live-order and level maps.
They do not copy the book or change event ordering.

Events must already be ordered. The book tracks visible orders for one symbol,
not the user's simulated account. Depth limits snapshots while the full book stays
stored. An apply failure makes that instance unsuitable for continued use.

Run `make test`. Independent Python expectations check per-event levels, same-price
orders, quantity errors, unknown IDs and restore behavior.

[Headers](../../include/itch/book/README.md) · [Native map](../README.md)
