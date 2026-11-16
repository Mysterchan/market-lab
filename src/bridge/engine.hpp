#pragma once

#include "itch/book/book.hpp"
#include <fstream>
#include <optional>
#include <string>

namespace itch::bridge {
struct Engine {
    explicit Engine(const char* name) : symbol(name), book(name) {}

    std::string symbol, output, error;
    Book book;
    std::optional<uint16_t> locate;
    bool failed = false;
    std::ifstream input;
    std::optional<Bytes> pending;
    uint64_t frames = 0, byte_offset = 0;
    bool last_end_messages = false, allow_implicit_eof = false, ended = false;
};

struct Applied {
    uint64_t timestamp = 0, match = 0, price = 0, quantity = 0, broken = 0;
    char type = 0, trading = 0, system = 0;
    bool selected = false;
    unsigned char flags = 0;
    std::optional<Snapshot> book;
};

/** Apply a validated-length payload using the shared decoder/book and trade rules.
 * Throws on invalid fields/book transitions; discard the owning engine on error.
 * No JSON, file output or simulated financial operations are performed here.
 */
Applied apply(Engine& engine, const Bytes& payload, uint64_t frame, size_t depth);
} // namespace itch::bridge
