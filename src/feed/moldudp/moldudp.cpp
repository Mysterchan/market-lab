#include "itch/feed/moldudp/moldudp.hpp"
#include "itch/common/binary.hpp"
#include "itch/feed/itch/decoder.hpp"
#include <stdexcept>
#include <utility>

namespace itch {
MoldDatagram parse_moldudp64(const unsigned char* packet, size_t length,
                             const std::string& expected_session) {
    if (length < 20) {
        throw std::runtime_error("truncated MoldUDP64 header");
    }
    if (std::string(reinterpret_cast<const char*>(packet), 10) != expected_session) {
        throw std::runtime_error("session mismatch; new session requires a fresh book");
    }
    const auto sequence = read_big_endian(packet + 10, 8);
    const auto count = read_big_endian(packet + 18, 2);
    if (!sequence || sequence == UINT64_MAX) {
        throw std::runtime_error("invalid sequence");
    }
    const bool ending = count == 65535;
    const auto messages = ending ? 0 : count;
    if (messages > UINT64_MAX - sequence || sequence + messages == UINT64_MAX) {
        throw std::runtime_error("sequence overflow");
    }
    MoldDatagram result{sequence, sequence + messages, ending, {}};
    size_t offset = 20;
    for (uint64_t i = 0; i < messages; ++i) {
        if (offset + 2 > length) {
            throw std::runtime_error("truncated message length");
        }
        const auto size = static_cast<size_t>(read_big_endian(packet + offset, 2));
        offset += 2;
        if (!size || size > length - offset) {
            throw std::runtime_error("truncated/empty ITCH message");
        }
        Bytes bytes(packet + offset, packet + offset + size);
        decode(bytes, sequence + i);
        result.messages.push_back(std::move(bytes));
        offset += size;
    }
    if (offset != length) {
        throw std::runtime_error("trailing bytes in MoldUDP64 datagram");
    }
    return result;
}
} // namespace itch
