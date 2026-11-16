# CSV and output publication

[csv.hpp](csv.hpp) declares csv_header() and csv_line(); the caller adds newlines
and checks stream errors. Prices use four decimal places; missing bid/ask fields stay empty.
[staged_output.hpp](staged_output.hpp) declares StagedOutput: write a temporary file,
validate it, then explicitly commit. Checkpoint-referenced journals can be retained.

Formatting does not update market state. Rename does not form a multi-file transaction
with a checkpoint or promise power-loss durability. Ordinary exceptions clean temporary
files; forced process termination may leave them behind.

[Implementation](../../../src/output/README.md)
