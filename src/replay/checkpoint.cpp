#include "itch/replay/checkpoint.hpp"
#include "itch/common/binary.hpp"
#include "itch/common/file_utils.hpp"
#include "itch/feed/itch/decoder.hpp"
#include "itch/output/csv.hpp"
#include "itch/output/staged_output.hpp"
#include <algorithm>
#include <array>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <limits>
#include <stdexcept>

namespace itch {
namespace {
[[noreturn]] void invalid(const std::string& reason) {
    throw std::runtime_error("checkpoint: " + reason);
}

void text_field(std::string& out, const std::string& value) {
    append_big_endian(out, value.size());
    out += value;
}

class Reader {
    const std::string& bytes_;
    size_t pos_ = 0;

  public:
    explicit Reader(const std::string& bytes) : bytes_(bytes) {}

    size_t remaining() const {
        return bytes_.size() - pos_;
    }

    std::string take(size_t n) {
        if (n > remaining()) {
            invalid("truncated state");
        }
        auto value = bytes_.substr(pos_, n);
        pos_ += n;
        return value;
    }

    uint64_t number(size_t width = 8) {
        const auto bytes = take(width);
        return read_big_endian(reinterpret_cast<const unsigned char*>(bytes.data()), width);
    }

    bool flag() {
        const auto n = number(1);
        if (n > 1) {
            invalid("invalid flag");
        }
        return n != 0;
    }

    std::string field(size_t maximum) {
        const auto n = number();
        if (n > maximum) {
            invalid("invalid string length");
        }
        return take(n);
    }
};
} // namespace

std::string encode_checkpoint(const Checkpoint& c) {
    std::string out = "ITCHCP01";
    append_big_endian(out, c.csv_path.empty() ? 1 : 2, 4);
    append_big_endian(out, c.input.size);
    text_field(out, c.input.digest);
    text_field(out, c.target);
    append_big_endian(out, c.next_frame);
    append_big_endian(out, c.offset);
    append_big_endian(out, c.selected);
    append_big_endian(out, c.hash);
    append_big_endian(out, c.depth);
    append_big_endian(out, c.with_offset, 1);
    append_big_endian(out, c.allow_implicit_eof, 1);
    append_big_endian(out, c.last_end_messages, 1);
    append_big_endian(out, c.book.locate.has_value(), 1);
    append_big_endian(out, c.book.locate.value_or(0), 2);
    append_big_endian(out, c.book.orders.size());
    for (const auto& o : c.book.orders) {
        append_big_endian(out, o.id);
        append_big_endian(out, static_cast<unsigned char>(o.side), 1);
        append_big_endian(out, o.shares, 4);
        append_big_endian(out, o.price, 4);
    }
    for (const auto* levels : {&c.book.bids, &c.book.asks}) {
        append_big_endian(out, levels->size());
        for (const auto& l : *levels) {
            append_big_endian(out, l.price, 4);
            append_big_endian(out, l.shares);
        }
    }
    if (!c.csv_path.empty()) {
        text_field(out, c.csv_path);
        append_big_endian(out, c.csv_bytes);
    }
    return out + sha256(out);
}

Checkpoint decode_checkpoint(const std::string& bytes) {
    if (bytes.size() < 76) {
        invalid("truncated file");
    }
    const auto body = bytes.substr(0, bytes.size() - 64);
    if (sha256(body) != bytes.substr(bytes.size() - 64)) {
        invalid("checksum mismatch");
    }
    Reader r(body);
    if (r.take(8) != "ITCHCP01") {
        invalid("invalid magic");
    }
    const auto version = r.number(4);
    if (version != 1 && version != 2) {
        invalid("unsupported version");
    }
    Checkpoint c;
    c.input.size = r.number();
    c.input.digest = r.field(64);
    c.target = r.field(8);
    if (c.input.digest.size() != 64 ||
        c.input.digest.find_first_not_of("0123456789abcdef") != std::string::npos) {
        invalid("invalid input SHA-256");
    }
    if (c.target.empty()) {
        invalid("empty symbol");
    }
    c.next_frame = r.number();
    c.offset = r.number();
    c.selected = r.number();
    c.hash = r.number();
    const auto depth = r.number();
    if (!depth || depth > 1000) {
        invalid("invalid depth");
    }
    c.depth = depth;
    c.with_offset = r.flag();
    c.allow_implicit_eof = r.flag();
    c.last_end_messages = r.flag();
    const bool has_locate = r.flag();
    const auto locate = r.number(2);
    if (has_locate) {
        c.book.locate = static_cast<uint16_t>(locate);
    } else if (locate) {
        invalid("locate without symbol mapping");
    }
    if (!c.next_frame || c.next_frame == std::numeric_limits<uint64_t>::max() ||
        c.offset > c.input.size || c.selected >= c.next_frame) {
        invalid("invalid replay counters");
    }
    const auto orders = r.number();
    if (orders > r.remaining() / 17) {
        invalid("invalid order count");
    }
    for (uint64_t i = 0; i < orders; ++i) {
        const auto id = r.number();
        const char side = static_cast<char>(r.number(1));
        const auto shares = r.number(4), price = r.number(4);
        c.book.orders.push_back(
            {id, side, static_cast<uint32_t>(shares), static_cast<uint32_t>(price)});
    }
    for (auto* levels : {&c.book.bids, &c.book.asks}) {
        const auto count = r.number();
        if (count > r.remaining() / 12) {
            invalid("invalid level count");
        }
        for (uint64_t i = 0; i < count; ++i) {
            const auto price = r.number(4), shares = r.number();
            levels->push_back({static_cast<uint32_t>(price), shares});
        }
    }
    if (version == 2) {
        c.csv_path = r.field(4096);
        c.csv_bytes = r.number();
        if (c.csv_path.empty() || c.csv_path.find('\0') != std::string::npos || !c.csv_bytes) {
            invalid("invalid CSV prefix binding");
        }
    }
    if (r.remaining()) {
        invalid("trailing state bytes");
    }
    Book book(c.target);
    book.restore(c.book, c.next_frame - 1);
    return c;
}

Checkpoint load_checkpoint(const std::string& path) {
    std::ifstream in(path, std::ios::binary);
    if (!in) {
        invalid("cannot open " + path);
    }
    const std::string bytes{std::istreambuf_iterator<char>(in), std::istreambuf_iterator<char>()};
    if (in.bad()) {
        invalid("read failed");
    }
    return decode_checkpoint(bytes);
}

void save_checkpoint(const Checkpoint& c, const std::string& path, const std::string& input) {
    const auto bytes = encode_checkpoint(c);
    // Round-trip validation before atomic publication.
    decode_checkpoint(bytes);
    StagedOutput output(path, input);
    output.stream().write(bytes.data(), static_cast<std::streamsize>(bytes.size()));
    output.commit([&bytes](const std::string& temporary) {
        if (encode_checkpoint(load_checkpoint(temporary)) != bytes) {
            invalid("temporary file round-trip mismatch");
        }
    });
}

void validate_boundary(const Checkpoint& c, const std::string& input) {
    std::ifstream in(input, std::ios::binary);
    if (!in) {
        invalid("cannot open input");
    }
    uint64_t offset = 0, frames = 0;
    bool last = false;
    while (offset < c.offset) {
        unsigned char prefix[2];
        in.read(reinterpret_cast<char*>(prefix), 2);
        if (in.gcount() != 2) {
            invalid("offset is not a frame boundary");
        }
        const size_t length = (static_cast<size_t>(prefix[0]) << 8) | prefix[1];
        if (!length || length + 2 > c.offset - offset) {
            invalid("offset is not a frame boundary");
        }
        Bytes bytes(length);
        in.read(reinterpret_cast<char*>(bytes.data()), length);
        if (in.gcount() != static_cast<std::streamsize>(length)) {
            invalid("truncated input before offset");
        }
        decode(bytes, ++frames);
        last = bytes.size() == 12 && bytes[0] == 'S' && bytes[1] == 0 && bytes[2] == 0 &&
               bytes[11] == 'C';
        offset += 2 + length;
    }
    if (frames != c.next_frame - 1 || last != c.last_end_messages) {
        invalid("frame/offset or end-message state mismatch");
    }
}

void copy_checkpoint_csv(const Checkpoint& c, std::ostream& output) {
    if (c.csv_path.empty()) {
        invalid("no saved CSV prefix; use suffix replay or a CSV-bound checkpoint");
    }
    std::ifstream in(c.csv_path, std::ios::binary);
    if (!in) {
        invalid("cannot open saved CSV prefix: " + c.csv_path);
    }
    const std::string header = std::string(csv_header(c.with_offset)) + "\n";
    if (c.csv_bytes < header.size()) {
        invalid("invalid CSV prefix length");
    }
    std::string actual(header.size(), '\0');
    in.read(actual.data(), static_cast<std::streamsize>(actual.size()));
    if (actual != header || in.gcount() != static_cast<std::streamsize>(header.size())) {
        invalid("CSV prefix header mismatch");
    }
    output << header;
    uint64_t remaining = c.csv_bytes - header.size(), rows = 0, hash = 14695981039346656037ULL;
    char last = '\n';
    std::array<char, 65536> buffer;
    while (remaining) {
        const auto count =
            static_cast<std::streamsize>(std::min<uint64_t>(remaining, buffer.size()));
        in.read(buffer.data(), count);
        if (in.gcount() != count) {
            invalid("truncated CSV prefix");
        }
        hash = fnv1a_bytes(hash, std::string_view(buffer.data(), static_cast<size_t>(count)));
        for (std::streamsize i = 0; i < count; ++i) {
            const auto ch = static_cast<unsigned char>(buffer[i]);
            if (ch == '\n') {
                ++rows;
            }
        }
        last = buffer[static_cast<size_t>(count) - 1];
        output.write(buffer.data(), count);
        remaining -= static_cast<uint64_t>(count);
    }
    if (last != '\n' || rows != c.selected || hash != c.hash) {
        invalid("CSV prefix row count/hash mismatch");
    }
    if (!output) {
        invalid("CSV prefix copy failed");
    }
    // Bytes written after the saved boundary were never checkpointed and are ignored.
}
} // namespace itch
