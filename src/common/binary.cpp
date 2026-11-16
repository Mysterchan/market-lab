#include "itch/common/binary.hpp"
#include <array>
#include <stdexcept>

namespace itch {
namespace {
void validate_width(size_t width) {
    if (width == 0 || width > 8) {
        throw std::invalid_argument("big-endian width must be 1..8");
    }
}
} // namespace

uint64_t read_big_endian(const unsigned char* bytes, size_t width) {
    validate_width(width);
    uint64_t value = 0;
    for (size_t i = 0; i < width; ++i) {
        value = (value << 8) | bytes[i];
    }
    return value;
}

void write_big_endian(unsigned char* bytes, uint64_t value, size_t width) {
    validate_width(width);
    while (width > 0) {
        bytes[--width] = static_cast<unsigned char>(value);
        value >>= 8;
    }
}

void append_big_endian(std::string& output, uint64_t value, size_t width) {
    std::array<unsigned char, 8> bytes{};
    write_big_endian(bytes.data(), value, width);
    output.append(reinterpret_cast<const char*>(bytes.data()), width);
}
} // namespace itch
