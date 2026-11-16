#pragma once

#include "itch/feed/itch/event.hpp"
#include <cstdint>
#include <istream>
#include <optional>

namespace itch {
/** Read one complete BinaryFILE payload at the current verified boundary.
 * Shared by v1 replay and the incremental market bridge. Counters also support
 * validated checkpoint resume. No seeking or resynchronization is attempted.
 * An absent terminator is accepted only immediately after a complete S/C.
 * Throws on incomplete framing, trailing data or an invalid stream.
 */
std::optional<Bytes> read_binary_frame(std::istream& input, uint64_t& frames, uint64_t& byte_offset,
                                       bool& last_end_messages, bool allow_implicit_eof);
} // namespace itch
