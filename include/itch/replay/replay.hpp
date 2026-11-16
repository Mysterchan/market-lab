#pragma once

#include "itch/replay/checkpoint.hpp"
#include <cstddef>
#include <cstdint>
#include <functional>
#include <iosfwd>
#include <optional>
#include <string>

namespace itch {

/** Counters/digest for the selected replay scope; emitted excludes any restored CSV prefix. */
struct Result {
    uint64_t frames = 0;
    uint64_t selected = 0;
    uint64_t hash = fnv1a_offset_basis;
    uint64_t event_ns = 0;
    uint64_t max_event_ns = 0;
    uint64_t elapsed_ns = 0;
    uint64_t emitted = 0;
    bool stopped = false;
};

/** Optional offline resume, stop boundary and checkpoint publication callback. */
struct ReplayControl {
    const Checkpoint* resume = nullptr;
    std::optional<uint64_t> stop_after_frame;
    Checkpoint* checkpoint = nullptr;
    uint64_t checkpoint_every_frames = 0;
    std::function<void(const Checkpoint&)> on_checkpoint;
    bool omit_header = false;
};

/** Replay a finite BinaryFILE using the shared decoder and book.
 * output may be null for validation-only replay. Output can contain a partial prefix on failure;
 * atomic publication is the caller's responsibility. Checkpoint callbacks run at frame boundaries.
 * Throws std::runtime_error for invalid input, incompatible resume state or unreachable stop
 * frames.
 */
Result replay(const std::string& path, const std::string& target, std::ostream* output,
              size_t max_depth, bool check_book, bool allow_implicit_eof = false,
              bool with_offset = false, const ReplayControl& control = {});

} // namespace itch
