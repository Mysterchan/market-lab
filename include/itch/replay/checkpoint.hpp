#pragma once
#include "itch/book/book.hpp"
#include "itch/common/digest.hpp"
#include "itch/common/file_utils.hpp"
#include <cstddef>
#include <cstdint>
#include <iosfwd>
#include <string>

namespace itch {
/** Versioned offline replay state; CSV journal binding is optional. */
struct Checkpoint {
    Fingerprint input{};
    std::string target;
    uint64_t next_frame = 1;
    uint64_t offset = 0;
    uint64_t selected = 0;
    uint64_t hash = fnv1a_offset_basis;
    size_t depth = 5;
    bool with_offset = false;
    bool allow_implicit_eof = false;
    bool last_end_messages = false;
    BookState book;
    // Optional v2 binding to a durable, complete CSV prefix.
    std::string csv_path;
    uint64_t csv_bytes = 0;
};

/** Encode canonical checkpoint bytes with a SHA-256 trailer (v1, or v2 with CSV binding). */
std::string encode_checkpoint(const Checkpoint& checkpoint);
/** Validate checksum, schema and book invariants; throws std::runtime_error on corruption. */
Checkpoint decode_checkpoint(const std::string& bytes);
/** Read and validate a checkpoint file; throws on open/read or validation failure. */
Checkpoint load_checkpoint(const std::string& path);
/** Publish a checkpoint atomically beside its destination, refusing to overwrite the input. */
void save_checkpoint(const Checkpoint& checkpoint, const std::string& path,
                     const std::string& input);
/** Check that the saved offset/frame/end-marker state is a valid boundary of this input. */
void validate_boundary(const Checkpoint& checkpoint, const std::string& input);
/** Copy only the saved CSV prefix, verifying header, row count and historical FNV-1a digest.
 * Uncheckpointed tail bytes are ignored. A failure may already have written to output;
 * callers must use staged output to prevent publication of an invalid prefix.
 */
void copy_checkpoint_csv(const Checkpoint& checkpoint, std::ostream& output);
} // namespace itch
