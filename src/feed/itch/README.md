# ITCH message decoding

[decoder.cpp](decoder.cpp) checks the exact message length before reading fields,
decodes symbols and produces order events. Unknown/wrong-length messages fail;
recognized messages outside book updates are explicitly skipped.

Input is one payload, without a file length prefix or network header. The caller owns
source position, book state and session recovery. Both file and network paths use this
decoder. A new message type needs length rules and valid/invalid test cases together.

Run `make test`. Read the [input contract](../../../docs/reference/itch-input.md) and
[headers](../../../include/itch/feed/itch/README.md) before changing event meanings.
