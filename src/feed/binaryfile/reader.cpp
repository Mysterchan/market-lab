#include "itch/feed/binaryfile/reader.hpp"
#include <stdexcept>
#include <string>

namespace itch {
std::optional<Bytes> read_binary_frame(std::istream& input, uint64_t& frames, uint64_t& byte_offset,
                                       bool& last_end_messages, bool allow_implicit_eof) {
    unsigned char prefix[2];
    input.read(reinterpret_cast<char*>(prefix), 2);
    if (input.gcount() == 0 && input.eof() && allow_implicit_eof && last_end_messages) {
        return std::nullopt;
    }
    if (input.gcount() == 0 && input.eof()) {
        throw std::runtime_error("missing BinaryFILE end marker after frame " +
                                 std::to_string(frames) + " (incomplete session)");
    }
    if (input.gcount() != 2) {
        throw std::runtime_error("truncated frame length after frame " + std::to_string(frames));
    }
    const size_t length = (static_cast<size_t>(prefix[0]) << 8) | prefix[1];
    if (length == 0) {
        if (input.peek() != std::char_traits<char>::eof()) {
            throw std::runtime_error("bytes after BinaryFILE end marker after frame " +
                                     std::to_string(frames));
        }
        if (input.bad()) {
            throw std::runtime_error("BinaryFILE input read failed");
        }
        return std::nullopt;
    }
    ++frames;
    Bytes bytes(length);
    input.read(reinterpret_cast<char*>(bytes.data()), static_cast<std::streamsize>(length));
    if (input.gcount() != static_cast<std::streamsize>(length)) {
        throw std::runtime_error("frame " + std::to_string(frames) + ": truncated payload");
    }
    byte_offset += 2 + length;
    last_end_messages =
        bytes.size() == 12 && bytes[0] == 'S' && bytes[1] == 0 && bytes[2] == 0 && bytes[11] == 'C';
    return bytes;
}
} // namespace itch
