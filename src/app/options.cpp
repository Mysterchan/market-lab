#include "itch/app/options.hpp"
#include <algorithm>
#include <stdexcept>

namespace itch {
namespace {
constexpr const char* usage =
    "live: itch_replay --live --listen IPv4:PORT --source IPv4:PORT --recovery IPv4:PORT --session "
    "TEN_BYTES --symbol SYMBOL [--output CSV] [--depth N] [--check-book] [--idle-timeout-ms N] "
    "[--recovery-timeout-ms N] [--retry-ms N] [--max-buffer-messages N] [--max-buffer-bytes N]\n"
    "usage: itch_replay --input FILE --symbol SYMBOL [--output CSV] [--verify] [--depth N] "
    "[--check-book] [--allow-implicit-eof] [--with-offset] [--checkpoint-out FILE] "
    "[--checkpoint-every-frames N] [--stop-after-frame N] [--resume FILE] [--resume-csv]";

uint64_t parse_frame(const std::string& value, const std::string& option = "--stop-after-frame") {
    if (value.empty() || !std::all_of(value.begin(), value.end(), [](char c) {
            return c >= '0' && c <= '9';
        })) {
        throw std::runtime_error(option + " must be a nonnegative integer");
    }
    try {
        const auto n = std::stoull(value);
        if (n == UINT64_MAX) {
            throw std::out_of_range("frame");
        }
        return n;
    } catch (const std::exception&) {
        throw std::runtime_error(option + " must be a nonnegative integer below UINT64_MAX");
    }
}

size_t parse_depth(const std::string& value) {
    if (value.empty() || !std::all_of(value.begin(), value.end(), [](char c) {
            return c >= '0' && c <= '9';
        })) {
        throw std::runtime_error("--depth must be an integer from 1 to 1000");
    }
    unsigned long long requested = 0;
    try {
        requested = std::stoull(value);
    } catch (const std::exception&) {
        throw std::runtime_error("--depth must be an integer from 1 to 1000");
    }
    if (requested == 0 || requested > 1000) {
        throw std::runtime_error("--depth must be an integer from 1 to 1000");
    }
    return static_cast<size_t>(requested);
}

// Presence tracking belongs to CLI parsing, not to the library configuration.
struct ParsedOptions {
    Options options;
    bool network_options = false;
};

class Arguments {
  public:
    Arguments(int argc, char** argv) : argc_(argc), argv_(argv) {}

    bool next(std::string& argument) {
        if (index_ >= argc_) {
            return false;
        }
        argument = argv_[index_++];
        return true;
    }

    std::string value() {
        if (index_ >= argc_) {
            throw std::runtime_error(usage);
        }
        return argv_[index_++];
    }

  private:
    int argc_;
    char** argv_;
    int index_ = 1;
};

bool parse_shared_option(const std::string& arg, Arguments& args, Options& options) {
    if (arg == "--symbol") {
        options.book.target = args.value();
    } else if (arg == "--output") {
        options.output.path = args.value();
    } else if (arg == "--check-book") {
        options.book.check_book = true;
    } else if (arg == "--depth") {
        options.book.max_depth = parse_depth(args.value());
    } else {
        return false;
    }
    return true;
}

bool parse_replay_option(const std::string& arg, Arguments& args, ReplayOptions& options) {
    if (arg == "--input") {
        options.path = args.value();
    } else if (arg == "--verify") {
        options.verify = true;
    } else if (arg == "--allow-implicit-eof") {
        options.allow_implicit_eof = true;
    } else if (arg == "--with-offset") {
        options.with_offset = true;
    } else {
        return false;
    }
    return true;
}

bool parse_checkpoint_option(const std::string& arg, Arguments& args, CheckpointOptions& options) {
    if (arg == "--checkpoint-out") {
        options.path = args.value();
    } else if (arg == "--resume") {
        options.resume_path = args.value();
    } else if (arg == "--resume-csv") {
        options.resume_csv = true;
    } else if (arg == "--checkpoint-every-frames") {
        options.every_frames = parse_frame(args.value(), arg);
        if (!options.every_frames) {
            throw std::runtime_error("--checkpoint-every-frames must be positive");
        }
    } else if (arg == "--stop-after-frame") {
        options.stop_after_frame = parse_frame(args.value(), arg);
    } else {
        return false;
    }
    return true;
}

bool parse_network_option(const std::string& arg, Arguments& args, LiveOptions& options) {
    if (arg == "--listen") {
        options.listen = args.value();
    } else if (arg == "--source") {
        options.source = args.value();
    } else if (arg == "--recovery") {
        options.recovery = args.value();
    } else if (arg == "--session") {
        options.session = args.value();
    } else if (arg == "--idle-timeout-ms" || arg == "--recovery-timeout-ms" ||
               arg == "--retry-ms" || arg == "--max-buffer-messages" ||
               arg == "--max-buffer-bytes") {
        const auto value = parse_frame(args.value(), arg);
        if (!value || value > 1000000000) {
            throw std::runtime_error(arg + " must be 1..1000000000");
        }
        if (arg == "--idle-timeout-ms") {
            options.idle_ms = value;
        } else if (arg == "--recovery-timeout-ms") {
            options.recovery_ms = value;
        } else if (arg == "--retry-ms") {
            options.retry_ms = value;
        } else if (arg == "--max-buffer-messages") {
            options.max_messages = value;
        } else {
            options.max_bytes = value;
        }
    } else {
        return false;
    }
    return true;
}

ParsedOptions parse_arguments(int argc, char** argv) {
    ParsedOptions parsed;
    Arguments args(argc, argv);
    std::string arg;
    while (args.next(arg)) {
        if (arg == "--live") {
            parsed.options.live = true;
        } else if (parse_shared_option(arg, args, parsed.options) ||
                   parse_replay_option(arg, args, parsed.options.replay) ||
                   parse_checkpoint_option(arg, args, parsed.options.checkpoint)) {
            // The matching group consumed the option and any required value.
        } else if (parse_network_option(arg, args, parsed.options.network)) {
            parsed.network_options = true;
        } else {
            throw std::runtime_error(usage);
        }
    }
    return parsed;
}

void resolve_defaults(Options& options) {
    if (options.live) {
        return;
    }
    // Saving is requested by a destination, an interval, or an explicit stop.
    // CSV continuation never enables checkpoint writes on its own.
    if (options.checkpoint.path.empty() && !options.checkpoint.resume_path.empty() &&
        (options.checkpoint.every_frames || options.checkpoint.stop_after_frame)) {
        options.checkpoint.path = options.checkpoint.resume_path;
    }
}

void validate_shared_options(const Options& options) {
    if ((!options.live && options.replay.path.empty()) || options.book.target.empty() ||
        options.book.target.size() > 8) {
        throw std::runtime_error(usage);
    }
}

void validate_mode_options(const ParsedOptions& parsed) {
    const auto& options = parsed.options;
    if (options.live) {
        if (!options.replay.path.empty() || options.replay.verify ||
            options.replay.allow_implicit_eof || options.replay.with_offset ||
            !options.checkpoint.path.empty() || !options.checkpoint.resume_path.empty() ||
            options.checkpoint.resume_csv || options.checkpoint.stop_after_frame ||
            options.checkpoint.every_frames) {
            throw std::runtime_error(
                "live mode cannot use file replay/checkpoint/verify/offset options");
        }
        if (options.network.listen.empty() || options.network.source.empty() ||
            options.network.recovery.empty() || options.network.session.size() != 10) {
            throw std::runtime_error("--live requires --listen IPv4:PORT --source IPv4:PORT "
                                     "--recovery IPv4:PORT --session TEN_BYTES");
        }
    } else if (parsed.network_options) {
        throw std::runtime_error("network options require --live");
    }
}

void validate_checkpoint_options(const Options& options) {
    if (options.checkpoint.resume_csv) {
        if (options.checkpoint.resume_path.empty() || options.output.path.empty()) {
            throw std::runtime_error("--resume-csv requires --resume and --output");
        }
    }
    if (options.checkpoint.every_frames && options.checkpoint.path.empty()) {
        throw std::runtime_error("--checkpoint-every-frames requires --checkpoint-out");
    }
    if (options.checkpoint.stop_after_frame && options.checkpoint.path.empty()) {
        throw std::runtime_error("--stop-after-frame requires --checkpoint-out");
    }
}

} // namespace

Options parse_options(int argc, char** argv) {
    auto parsed = parse_arguments(argc, argv);
    resolve_defaults(parsed.options);
    validate_shared_options(parsed.options);
    validate_mode_options(parsed);
    validate_checkpoint_options(parsed.options);
    return parsed.options;
}

} // namespace itch
