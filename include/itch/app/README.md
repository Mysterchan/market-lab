# Native command-line settings

[options.hpp](options.hpp) declares Options and parse_options(). Settings are grouped
by book, output, network, replay and checkpoint responsibilities. Parsing resolves
defaults and validates combinations before any input or output is opened.

Network settings use LiveOptions. Add an option in parsing/validation, then connect
it in main; argument parsing must not update a book or publish output.

[Implementation](../../../src/app/README.md) · [Interface map](../README.md)
