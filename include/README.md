# C++ interfaces

[itch/](itch/README.md) contains headers for the native engine. Include them from the
repository's `include/` root, for example `#include "itch/feed/itch/decoder.hpp"`.
Implementations live under [src/](../src/README.md).

Headers explain inputs, results, failures and side effects. Keep helpers used by one
implementation file in its anonymous namespace. Folder boundaries do not create
separate libraries or namespaces; Make and CMake list the actual build targets.
