#pragma once

#include "itch/feed/itch/event.hpp"

#include <cstddef>
#include <cstdint>
#include <optional>

namespace itch {

/** Return the exact supported ITCH 5.0 payload length; zero means an unknown type. */
size_t expected_length(char type);
/** Validate one complete ITCH payload and decode a book-relevant event.
 * @param frame Source record/sequence identifier used in diagnostics and snapshots.
 * @return An event, or nullopt for a recognized message deliberately skipped by the book.
 * @throws std::runtime_error for an unknown type or incorrect payload length.
 */
std::optional<Event> decode(const Bytes& bytes, uint64_t frame);

} // namespace itch
