#include "itch/replay/replay.hpp"

#include "itch/book/book.hpp"
#include "itch/feed/binaryfile/reader.hpp"
#include "itch/feed/itch/decoder.hpp"
#include "itch/output/csv.hpp"

#include <algorithm>
#include <chrono>
#include <fstream>
#include <ostream>
#include <stdexcept>
#include <string>

namespace itch {
namespace {

using Clock = std::chrono::steady_clock;

} // namespace

Result replay(const std::string& path, const std::string& target, std::ostream* output,
              size_t max_depth, bool check_book, bool allow_implicit_eof, bool with_offset,
              const ReplayControl& control) {
    const auto begin = Clock::now();
    std::optional<Fingerprint> input;
    if (control.resume || control.checkpoint || control.on_checkpoint) {
        input = fingerprint(path);
    }
    std::ifstream in(path, std::ios::binary);
    if (!in) {
        throw std::runtime_error("cannot open input: " + path);
    }
    Book book(target);
    Result result;
    uint64_t byte_offset = 0;
    bool last_was_end_of_messages = false;
    if (control.resume) {
        const auto& c = *control.resume;
        if (c.target != target) {
            throw std::runtime_error("checkpoint symbol mismatch");
        }
        if (c.depth != max_depth || c.with_offset != with_offset ||
            c.allow_implicit_eof != allow_implicit_eof) {
            throw std::runtime_error(
                "checkpoint replay options mismatch (depth/with-offset/allow-implicit-eof)");
        }
        if (c.input.size != input->size || c.input.digest != input->digest) {
            throw std::runtime_error("checkpoint input SHA-256 mismatch");
        }
        validate_boundary(c, path);
        book.restore(c.book, c.next_frame - 1);
        result.frames = c.next_frame - 1;
        result.selected = c.selected;
        result.hash = c.hash;
        byte_offset = c.offset;
        last_was_end_of_messages = c.last_end_messages;
        in.seekg(static_cast<std::streamoff>(byte_offset));
        if (!in) {
            throw std::runtime_error("checkpoint input seek failed");
        }
    }
    if (control.stop_after_frame && *control.stop_after_frame < result.frames) {
        throw std::runtime_error("--stop-after-frame precedes checkpoint");
    }
    if (output && !control.omit_header) {
        *output << csv_header(with_offset) << '\n';
    }

    auto checkpoint_state = [&]() {
        return Checkpoint{*input,
                          target,
                          result.frames + 1,
                          byte_offset,
                          result.selected,
                          result.hash,
                          max_depth,
                          with_offset,
                          allow_implicit_eof,
                          last_was_end_of_messages,
                          book.state(),
                          {},
                          0};
    };
    const uint64_t starting_frame = result.frames;

    uint64_t attempted_offset = byte_offset;
    const uint64_t starting_offset = byte_offset;
    try {
        while (true) {
            if (control.on_checkpoint && control.checkpoint_every_frames &&
                result.frames > starting_frame &&
                result.frames % control.checkpoint_every_frames == 0) {
                control.on_checkpoint(checkpoint_state());
            }
            if (control.stop_after_frame && result.frames == *control.stop_after_frame) {
                result.stopped = true;
                break;
            }
            const uint64_t frame_offset = byte_offset;
            attempted_offset = frame_offset;
            auto payload = read_binary_frame(in, result.frames, byte_offset,
                                             last_was_end_of_messages, allow_implicit_eof);
            if (!payload) {
                break;
            }
            auto event = decode(*payload, result.frames);
            if (!event) {
                continue;
            }
            const auto event_begin = Clock::now();
            auto snapshot = book.apply(*event, max_depth, check_book);
            if (!snapshot) {
                continue;
            }
            snapshot->byte_offset = frame_offset;
            snapshot->byte_end_offset = byte_offset;
            const std::string line = csv_line(*snapshot, with_offset);
            const uint64_t event_ns =
                std::chrono::duration_cast<std::chrono::nanoseconds>(Clock::now() - event_begin)
                    .count();
            ++result.selected;
            ++result.emitted;
            result.event_ns += event_ns;
            result.max_event_ns = std::max(result.max_event_ns, event_ns);
            result.hash = hash_csv_line(result.hash, line);
            if (output) {
                *output << line << '\n';
            }
        }
    } catch (const std::exception& error) {
        throw std::runtime_error(
            std::string(error.what()) +
            "; first untrusted byte_offset=" + std::to_string(attempted_offset) +
            "; replay started at byte_offset=" + std::to_string(starting_offset));
    }

    if (control.stop_after_frame && !result.stopped) {
        throw std::runtime_error("--stop-after-frame exceeds input frame count");
    }
    if (input) {
        const auto final_input = fingerprint(path);
        if (final_input.size != input->size || final_input.digest != input->digest) {
            throw std::runtime_error("input changed during replay (SHA-256 mismatch)");
        }
    }
    if (control.checkpoint) {
        *control.checkpoint = checkpoint_state();
    }

    result.elapsed_ns =
        std::chrono::duration_cast<std::chrono::nanoseconds>(Clock::now() - begin).count();
    return result;
}

} // namespace itch
