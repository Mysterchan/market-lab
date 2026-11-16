// Small C ABI: one opaque book per Python session; strings live until next call.
#include "engine.hpp"
#include "itch/common/binary.hpp"
#include "itch/feed/binaryfile/reader.hpp"
#include "itch/feed/itch/decoder.hpp"
#include <fstream>
#include <sstream>
#include <stdexcept>

namespace {
using itch::bridge::Engine;

void levels(std::ostream& out, const std::vector<itch::Level>& values) {
    out << '[';
    bool first = true;
    for (const auto& v : values) {
        if (!first) {
            out << ',';
        }
        first = false;
        out << '[' << v.price << ',' << v.shares << ']';
    }
    out << ']';
}

void prefetch(Engine& e) {
    if (e.failed) {
        throw std::runtime_error("discard failed market engine");
    }
    if (!e.input.is_open()) {
        throw std::runtime_error("no BinaryFILE is open");
    }
    if (e.pending || e.ended) {
        return;
    }
    const auto offset = e.byte_offset;
    try {
        e.pending = itch::read_binary_frame(e.input, e.frames, e.byte_offset, e.last_end_messages,
                                            e.allow_implicit_eof);
        if (e.pending) {
            itch::decode(*e.pending, e.frames); // validate without applying to Book
        } else {
            e.ended = true;
        }
    } catch (const std::exception& ex) {
        throw std::runtime_error(std::string(ex.what()) +
                                 "; first untrusted byte_offset=" + std::to_string(offset));
    }
}
} // namespace

extern "C" {
void* market_create(const char* symbol) noexcept {
    try {
        return new Engine(symbol);
    } catch (...) {
        return nullptr;
    }
}

void market_destroy(void* handle) noexcept {
    delete static_cast<Engine*>(handle);
}

const char* market_error(void* handle) noexcept {
    return static_cast<Engine*>(handle)->error.c_str();
}

int market_open_file(void* handle, const char* path, int allow_implicit_eof) noexcept {
    auto& e = *static_cast<Engine*>(handle);
    try {
        if (e.failed || e.input.is_open() || e.locate) {
            throw std::runtime_error("open requires an unused market engine");
        }
        e.input.open(path, std::ios::binary);
        if (!e.input) {
            throw std::runtime_error(std::string("cannot open input: ") + path);
        }
        e.allow_implicit_eof = allow_implicit_eof != 0;
        return 1;
    } catch (const std::exception& ex) {
        e.failed = true;
        e.error = ex.what();
        return 0;
    } catch (...) {
        e.failed = true;
        e.error = "native file open failure";
        return 0;
    }
}

const char* market_peek_file(void* handle) noexcept {
    auto& e = *static_cast<Engine*>(handle);
    try {
        prefetch(e);
        if (e.ended) {
            e.output = "null";
        } else {
            const auto& b = *e.pending;
            e.output = "{\"sequence\":" + std::to_string(e.frames) + ",\"timestamp_ns\":" +
                       std::to_string(itch::read_big_endian(b.data() + 5, 6)) + "}";
        }
        return e.output.c_str();
    } catch (const std::exception& ex) {
        e.failed = true;
        e.error = ex.what();
        return nullptr;
    } catch (...) {
        e.failed = true;
        e.error = "native file peek failure";
        return nullptr;
    }
}

const char* market_inspect(void* handle, const unsigned char* data, size_t size,
                           uint64_t frame) noexcept {
    auto& e = *static_cast<Engine*>(handle);
    try {
        if (e.failed) {
            throw std::runtime_error("discard failed market engine");
        }
        itch::Bytes b(data, data + size);
        itch::decode(b, frame);
        e.output = "{\"sequence\":" + std::to_string(frame) +
                   ",\"timestamp_ns\":" + std::to_string(itch::read_big_endian(b.data() + 5, 6)) +
                   "}";
        return e.output.c_str();
    } catch (const std::exception& ex) {
        e.failed = true;
        e.error = ex.what();
        return nullptr;
    } catch (...) {
        e.failed = true;
        e.error = "native inspect failure";
        return nullptr;
    }
}

const char* market_apply(void* handle, const unsigned char* data, size_t size, uint64_t frame,
                         size_t depth) noexcept {
    auto& e = *static_cast<Engine*>(handle);
    try {
        if (e.failed) {
            throw std::runtime_error("discard failed market engine");
        }
        itch::Bytes b(data, data + size);
        const auto result = itch::bridge::apply(e, b, frame, depth);
        std::ostringstream out;
        out << "{\"sequence\":" << frame << ",\"timestamp_ns\":" << result.timestamp
            << ",\"source_type\":\"" << result.type
            << "\",\"selected\":" << (result.selected ? "true" : "false") << ",\"book\":";
        if (result.book) {
            const auto& snap = *result.book;
            out << "{\"bids\":";
            levels(out, snap.bids);
            out << ",\"asks\":";
            levels(out, snap.asks);
            out << ",\"bid_levels\":" << snap.bid_levels << ",\"ask_levels\":" << snap.ask_levels
                << '}';
        } else {
            out << "null";
        }
        out << ",\"trade\":";
        if (result.flags & 1) {
            out << "{\"match_id\":" << result.match << ",\"quantity\":" << result.quantity
                << ",\"price\":" << result.price
                << ",\"printable\":" << ((result.flags & 2) ? "true" : "false") << '}';
        } else {
            out << "null";
        }
        out << ",\"broken_match_id\":";
        if (result.flags & 4) {
            out << result.broken;
        } else {
            out << "null";
        }
        out << ",\"trading_state\":";
        if (result.trading) {
            out << '"' << result.trading << '"';
        } else {
            out << "null";
        }
        out << ",\"system_event\":";
        if (result.system) {
            out << '"' << result.system << '"';
        } else {
            out << "null";
        }
        out << '}';
        e.output = out.str();
        return e.output.c_str();
    } catch (const std::exception& ex) {
        e.failed = true;
        e.error = ex.what();
        return nullptr;
    } catch (...) {
        e.failed = true;
        e.error = "native engine failure";
        return nullptr;
    }
}

const char* market_next_file(void* handle, size_t depth) noexcept {
    auto& e = *static_cast<Engine*>(handle);
    try {
        prefetch(e);
        if (e.ended) {
            e.output = "null";
            return e.output.c_str();
        }
        const auto& b = *e.pending;
        const auto* result = market_apply(handle, b.data(), b.size(), e.frames, depth);
        if (result) {
            e.pending.reset();
        }
        return result;
    } catch (const std::exception& ex) {
        e.failed = true;
        e.error = ex.what();
        return nullptr;
    } catch (...) {
        e.failed = true;
        e.error = "native file step failure";
        return nullptr;
    }
}
}
