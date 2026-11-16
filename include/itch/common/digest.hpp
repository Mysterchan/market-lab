#pragma once
#include <cstdint>
#include <string>
#include <string_view>

namespace itch {
inline constexpr uint64_t fnv1a_offset_basis = 14695981039346656037ULL;
/** Extend a non-cryptographic FNV-1a digest with exact bytes, including any newlines. */
uint64_t fnv1a_bytes(uint64_t hash, std::string_view bytes);
/** Extend the CSV data-row digest with a line and exactly one trailing newline.
 * This preserves the historical checkpoint/replay digest format; it excludes the CSV header.
 */
uint64_t hash_csv_line(uint64_t hash, std::string_view line);

/** Return the lowercase 64-character SHA-256 digest of exact in-memory bytes. */
std::string sha256(const std::string& bytes);

/** Byte length and SHA-256 identity of a complete file. */
struct Fingerprint {
    uint64_t size;
    std::string digest;
};

/** Read and hash a complete file. Throws std::runtime_error on open/read failure. */
Fingerprint fingerprint(const std::string& path);
} // namespace itch
