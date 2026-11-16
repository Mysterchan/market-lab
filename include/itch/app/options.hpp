#pragma once
#include "itch/feed/moldudp/live.hpp"
#include <cstddef>
#include <cstdint>
#include <optional>
#include <string>

namespace itch {
/** Settings shared by live input and file replay. */
struct BookOptions {
    std::string target;
    bool check_book = false;
    size_t max_depth = 5;
};

struct OutputOptions {
    std::string path;
};

struct ReplayOptions {
    std::string path;
    bool verify = false;
    bool allow_implicit_eof = false;
    bool with_offset = false;
};

struct CheckpointOptions {
    std::string path;
    std::string resume_path;
    std::optional<uint64_t> stop_after_frame;
    uint64_t every_frames = 0;
    bool resume_csv = false;
};

/** Validated app configuration; replay/checkpoint settings apply only to file mode.
 * CLI token tracking stays private to the parser, outside these settings.
 */
struct Options {
    bool live = false;
    BookOptions book;
    OutputOptions output;
    LiveOptions network;
    ReplayOptions replay;
    CheckpointOptions checkpoint;
};

/** Parse command-line arguments and validate mode combinations.
 * Resolves the default checkpoint destination when continuation requests saving.
 * @throws std::runtime_error for missing, invalid, or incompatible arguments.
 */
Options parse_options(int argc, char** argv);
} // namespace itch
