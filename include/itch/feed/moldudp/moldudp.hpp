#pragma once
#include "itch/feed/itch/event.hpp"
#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

namespace itch {
/** A structurally validated MoldUDP64 datagram; boundary is exclusive. */
struct MoldDatagram {
    uint64_t sequence;
    uint64_t boundary;
    bool ending;
    std::vector<Bytes> messages;
};

/** Parse and validate a whole datagram before the caller changes sequence/book state.
 * Heartbeats and end markers carry no payload. ITCH validation uses the existing decoder.
 * Caller must supply length readable bytes; expected_session must contain exactly ten bytes.
 * @throws std::runtime_error for a wrong session, malformed framing, overflow or invalid ITCH.
 */
MoldDatagram parse_moldudp64(const unsigned char* packet, size_t length,
                             const std::string& expected_session);
} // namespace itch
