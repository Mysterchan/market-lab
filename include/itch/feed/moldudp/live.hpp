#pragma once
#include "itch/replay/replay.hpp"
#include <cstddef>
#include <cstdint>
#include <iosfwd>
#include <string>

namespace itch {
/** Explicit unicast endpoints, expected session and bounded recovery settings. */
struct LiveOptions {
    std::string listen;
    std::string source;
    std::string recovery;
    std::string session;
    uint64_t idle_ms = 5000;
    uint64_t recovery_ms = 3000;
    uint64_t retry_ms = 100;
    size_t max_messages = 100000;
    size_t max_bytes = 16000000;
};

/** Receive a finite MoldUDP64 unicast session starting at sequence 1 with an empty book.
 * Buffers gaps and requests retransmission before applying later events. Status goes to stderr.
 * output receives CSV rows and may contain a valid prefix on failure; use StagedOutput for
 * publication. Throws std::runtime_error on protocol/book failure, resource limits or lack of
 * progress. Validated against local simulators only; no certified Nasdaq connection or network
 * checkpoints.
 */
Result live(const LiveOptions&, const std::string& target, std::ostream& output, size_t depth,
            bool check_book);
} // namespace itch
