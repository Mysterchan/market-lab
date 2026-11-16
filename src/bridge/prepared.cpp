// Batched prepared-market/v2 exporter. Python owns staging/publication/cancellation.
#include "prepared.hpp"
#include "engine.hpp"
#include "itch/common/binary.hpp"
#include "itch/feed/binaryfile/reader.hpp"
#include <chrono>
#include <sstream>
#include <stdexcept>

namespace {
using Clock = std::chrono::steady_clock;
using itch::bridge::Applied;

void number(std::string& output, uint64_t value, size_t width = 8) {
    for (size_t i = width; i > 0; --i) {
        output.push_back(static_cast<char>((value >> ((i - 1) * 8)) & 255));
    }
}

void text(std::string& output, const std::string& value) {
    output += value;
    output.append(16 - value.size(), '\0');
}

struct State {
    uint64_t time = UINT64_MAX, bid_levels = 0, ask_levels = 0;
    std::string trading = "unknown", system = "unknown";
    std::vector<itch::Level> bids, asks;

    void update(const Applied& event) {
        if (event.book) {
            time = event.timestamp;
            bids = event.book->bids;
            asks = event.book->asks;
            bid_levels = event.book->bid_levels;
            ask_levels = event.book->ask_levels;
        }
        if (event.trading) {
            trading = event.trading;
        }
        if (event.system) {
            system = event.system;
        }
    }

    std::string encode(size_t depth) const {
        std::string output;
        output.reserve(60 + 32 * depth);
        number(output, time);
        number(output, bid_levels);
        number(output, ask_levels);
        text(output, trading);
        text(output, system);
        number(output, bids.size(), 2);
        number(output, asks.size(), 2);
        for (const auto* side : {&bids, &asks}) {
            for (const auto& level : *side) {
                number(output, level.price);
                number(output, level.shares);
            }
            output.append((depth - side->size()) * 16, '\0');
        }
        return output;
    }
};

class Writer {
  public:
    itch::bridge::Engine engine;
    std::ofstream events, states;
    size_t depth;
    bool profile, done = false, failed = false, seen = false;
    uint64_t count = 0, version = 0, first = 0, last = 0;
    uint64_t read_ns = 0, apply_ns = 0, encode_write_ns = 0;
    std::string output, error;

    Writer(const char* source, const char* symbol, const char* event_path, const char* state_path,
           size_t requested_depth, bool implicit, bool timings)
        : engine(symbol), depth(requested_depth), profile(timings) {
        if (depth < 1 || depth > 100) {
            throw std::runtime_error("invalid prepared depth");
        }
        engine.input.open(source, std::ios::binary);
        if (!engine.input) {
            throw std::runtime_error("cannot open prepared source");
        }
        engine.allow_implicit_eof = implicit;
        events.exceptions(std::ios::badbit | std::ios::failbit);
        states.exceptions(std::ios::badbit | std::ios::failbit);
        events.open(event_path, std::ios::binary | std::ios::trunc);
        states.open(state_path, std::ios::binary | std::ios::trunc);
        previous = state.encode(depth);
        states.write(previous.data(), static_cast<std::streamsize>(previous.size()));
    }

    void next(size_t limit) {
        if (failed || done || !limit || limit > 8192) {
            throw std::runtime_error("invalid prepared batch state/size");
        }
        for (size_t i = 0; i < limit; ++i) {
            auto started = profile ? Clock::now() : Clock::time_point{};
            const auto offset = engine.byte_offset;
            auto payload =
                itch::read_binary_frame(engine.input, engine.frames, engine.byte_offset,
                                        engine.last_end_messages, engine.allow_implicit_eof);
            measure(started, read_ns);
            if (!payload) {
                if (!seen) {
                    throw std::runtime_error("requested symbol never appeared in input");
                }
                done = true;
                break;
            }
            started = profile ? Clock::now() : Clock::time_point{};
            // Shared apply validates lengths before field access; then check chronology.
            const auto event = itch::bridge::apply(engine, *payload, engine.frames, depth);
            const auto stamp = event.timestamp;
            if (event.timestamp >= 86400000000000ULL || (engine.frames > 1 && stamp < last)) {
                throw std::runtime_error("feed timestamp moved backwards/outside session at byte " +
                                         std::to_string(offset));
            }
            if (engine.frames == 1) {
                first = stamp;
            }
            last = stamp;
            seen = seen || event.selected;
            measure(started, apply_ns);
            started = profile ? Clock::now() : Clock::time_point{};
            if (event.selected || event.type == 'S') {
                append(event);
            }
            measure(started, encode_write_ns);
        }
        // Bound buffers by batch size. Streams are flushed only on completion.
        write_buffer(events, event_buffer);
        write_buffer(states, state_buffer);
        if (done) {
            events.flush();
            states.flush();
        }
        std::ostringstream result;
        result << "{\"source_frames\":" << engine.frames << ",\"frames\":" << count
               << ",\"states\":" << version + 1 << ",\"first_ns\":" << first
               << ",\"last_ns\":" << last << ",\"done\":" << (done ? "true" : "false")
               << ",\"read_ns\":" << read_ns << ",\"apply_ns\":" << apply_ns
               << ",\"encode_write_ns\":" << encode_write_ns << '}';
        output = result.str();
    }

  private:
    State state;
    std::string previous, event_buffer, state_buffer;

    void measure(Clock::time_point started, uint64_t& total) const {
        if (profile) {
            total += std::chrono::duration_cast<std::chrono::nanoseconds>(Clock::now() - started)
                         .count();
        }
    }

    void write_buffer(std::ofstream& stream, std::string& buffer) {
        const auto started = profile ? Clock::now() : Clock::time_point{};
        stream.write(buffer.data(), static_cast<std::streamsize>(buffer.size()));
        buffer.clear();
        measure(started, encode_write_ns);
    }

    void append(const Applied& event) {
        state.update(event);
        auto raw = state.encode(depth);
        if (raw != previous) {
            ++version;
            state_buffer += raw;
            previous = std::move(raw);
        }
        number(event_buffer, event.timestamp);
        number(event_buffer, engine.frames); // original source frame, not prepared ordinal
        number(event_buffer, version);
        event_buffer.push_back('M');
        event_buffer.push_back(event.type);
        number(event_buffer, event.match);
        number(event_buffer, event.price);
        number(event_buffer, event.quantity);
        event_buffer.push_back(static_cast<char>(event.flags));
        number(event_buffer, event.broken);
        ++count;
    }
};
} // namespace

extern "C" {
// create returns null on failure; no exceptions cross ABI. Paths must be owned staging files.
void* prepared_create(const char* source, const char* symbol, const char* events,
                      const char* states, size_t depth, int implicit, int profile) noexcept {
    try {
        return new Writer(source, symbol, events, states, depth, implicit != 0, profile != 0);
    } catch (...) {
        return nullptr;
    }
}

const char* prepared_next(void* handle, size_t frames) noexcept {
    auto& writer = *static_cast<Writer*>(handle);
    try {
        writer.next(frames);
        return writer.output.c_str();
    } catch (const std::exception& ex) {
        writer.failed = true;
        writer.error = ex.what();
    } catch (...) {
        writer.failed = true;
        writer.error = "native preparation failure";
    }
    return nullptr;
}

const char* prepared_error(void* handle) noexcept {
    return static_cast<Writer*>(handle)->error.c_str();
}

void prepared_destroy(void* handle) noexcept {
    delete static_cast<Writer*>(handle);
}
}
