# Native CLI and output coordination

[options.cpp](options.cpp) parses settings by book/output/network/replay/checkpoint group,
resolves defaults and validates combinations. Add a CLI option in the relevant parser,
default resolver and validator. Repeated options keep their last value.

[main.cpp](main.cpp) selects file or network input, coordinates staged CSV and checkpoint
journals, and reports results/errors. Book updates belong to Book, not argument parsing.
A nonzero exit means failure; a stdout prefix alone is not a complete result.

After a change, run `make test`. Check invalid/missing values, mutually exclusive modes,
checkpoint destination rules and preservation of existing output on failure.
Update both build source lists when adding a C++ file.

[Headers](../../include/itch/app/README.md) · [Native map](../README.md)
