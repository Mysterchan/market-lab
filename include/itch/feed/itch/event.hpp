#pragma once

#include <cstdint>
#include <string>
#include <variant>
#include <vector>

namespace itch {

using Bytes = std::vector<unsigned char>;

struct Directory {
    std::string symbol;
};

struct Add {
    std::string symbol;
    uint64_t id;
    char side;
    uint32_t shares;
    uint32_t price;
};

struct Reduce {
    uint64_t id;
    uint32_t shares;
};

struct Delete {
    uint64_t id;
};

struct Replace {
    uint64_t old_id, new_id;
    uint32_t shares, price;
};

using EventData = std::variant<Directory, Add, Reduce, Delete, Replace>;

/** Decoded ITCH-specific event, not a cross-provider market event contract. */
struct Event {
    uint64_t frame;
    uint64_t timestamp;
    char type;
    uint16_t locate;
    EventData data;
};

} // namespace itch
