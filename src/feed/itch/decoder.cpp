#include "itch/feed/itch/decoder.hpp"
#include "itch/common/binary.hpp"

#include <cstddef>
#include <stdexcept>
#include <string>

namespace itch {
namespace {

std::string symbol(const Bytes& bytes, size_t offset) {
    std::string value(bytes.begin() + static_cast<std::ptrdiff_t>(offset),
                      bytes.begin() + static_cast<std::ptrdiff_t>(offset + 8));
    while (!value.empty() && value.back() == ' ') {
        value.pop_back();
    }
    return value;
}

} // namespace

size_t expected_length(char type) {
    switch (type) {
    case 'R':
        return 39;
    case 'A':
        return 36;
    case 'F':
        return 40;
    case 'E':
        return 31;
    case 'C':
        return 36;
    case 'X':
        return 23;
    case 'D':
        return 19;
    case 'U':
        return 35;
    // Recognized ITCH 5.0 messages that do not change the displayed book.
    case 'S':
        return 12;
    case 'H':
        return 25;
    case 'Y':
        return 20;
    case 'L':
        return 26;
    case 'V':
        return 35;
    case 'W':
        return 12;
    case 'K':
        return 28;
    case 'J':
        return 35;
    case 'h':
        return 21;
    case 'P':
        return 44;
    case 'Q':
        return 40;
    case 'B':
        return 19;
    case 'I':
        return 50;
    case 'N':
        return 20;
    case 'O':
        return 48;
    default:
        return 0;
    }
}

std::optional<Event> decode(const Bytes& bytes, uint64_t frame) {
    if (bytes.empty()) {
        throw std::runtime_error("frame " + std::to_string(frame) + ": empty ITCH payload");
    }
    const char type = static_cast<char>(bytes[0]);
    const size_t length = expected_length(type);
    if (!length) {
        throw std::runtime_error("frame " + std::to_string(frame) + ": unknown ITCH type");
    }
    if (bytes.size() != length) {
        throw std::runtime_error("frame " + std::to_string(frame) + ": invalid " + type +
                                 " length");
    }

    // Length validation above makes every field read below safe.
    if (type != 'R' && type != 'A' && type != 'F' && type != 'E' && type != 'C' && type != 'X' &&
        type != 'D' && type != 'U') {
        return std::nullopt;
    }

    const uint16_t locate = static_cast<uint16_t>(read_big_endian(bytes.data() + 1, 2));
    const uint64_t timestamp = read_big_endian(bytes.data() + 5, 6);
    EventData data;
    if (type == 'R') {
        data = Directory{symbol(bytes, 11)};
    } else if (type == 'A' || type == 'F') {
        data = Add{symbol(bytes, 24), read_big_endian(bytes.data() + 11, 8),
                   static_cast<char>(bytes[19]),
                   static_cast<uint32_t>(read_big_endian(bytes.data() + 20, 4)),
                   static_cast<uint32_t>(read_big_endian(bytes.data() + 32, 4))};
    } else if (type == 'E' || type == 'C' || type == 'X') {
        data = Reduce{read_big_endian(bytes.data() + 11, 8),
                      static_cast<uint32_t>(read_big_endian(bytes.data() + 19, 4))};
    } else if (type == 'D') {
        data = Delete{read_big_endian(bytes.data() + 11, 8)};
    } else {
        data = Replace{read_big_endian(bytes.data() + 11, 8), read_big_endian(bytes.data() + 19, 8),
                       static_cast<uint32_t>(read_big_endian(bytes.data() + 27, 4)),
                       static_cast<uint32_t>(read_big_endian(bytes.data() + 31, 4))};
    }
    return Event{frame, timestamp, type, locate, std::move(data)};
}

} // namespace itch
