#pragma once

#include <cstddef>
#include <cstdint>
#include <string>

namespace itch {
/** Read an unsigned big-endian field. Caller must provide at least width readable bytes.
 * @throws std::invalid_argument when width is outside 1..8.
 */
uint64_t read_big_endian(const unsigned char* bytes, size_t width);

/** Write the low width bytes in big-endian order; destination must have width writable bytes.
 * @throws std::invalid_argument when width is outside 1..8.
 */
void write_big_endian(unsigned char* bytes, uint64_t value, size_t width);

/** Append the low width bytes to a binary string, without a length prefix. */
void append_big_endian(std::string& output, uint64_t value, size_t width = 8);
} // namespace itch
