# CSV formatting and file publication

[csv.cpp](csv.cpp) formats best prices, shares and depth, with optional file offsets.
It does not add a trailing newline. [staged_output.cpp](staged_output.cpp) owns a temporary
file beside the target, flush/fsync, optional validation and final rename.

Prices use four decimal places; missing sides stay empty. Output depth is not the count
of all stored levels. Rename is not a transaction across CSV and checkpoint files.
Exceptions clean temporary files; forced process termination may leave them behind.

Run `make test` after changing fields or newlines: CSV digests, checkpoint prefixes
and file/network equivalence depend on the exact output. Keep old output on failure.

[Headers](../../include/itch/output/README.md) · [Native map](../README.md)
