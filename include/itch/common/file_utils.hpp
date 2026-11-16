#pragma once
#include <string>

namespace itch {
/** Reject two paths naming the same file, including aliases and canonical paths.
 * Empty paths are ignored, so callers may pass unused optional destinations.
 * @throws std::runtime_error if paths conflict; filesystem errors may also propagate.
 */
void reject_same_file(const std::string& a, const std::string& b);
} // namespace itch
