#include "itch/common/file_utils.hpp"
#include <filesystem>
#include <stdexcept>

namespace itch {
void reject_same_file(const std::string& a, const std::string& b) {
    if (a.empty() || b.empty()) {
        return;
    }
    namespace fs = std::filesystem;
    std::error_code ec;
    const bool equivalent = fs::equivalent(a, b, ec);
    if ((!ec && equivalent) || fs::weakly_canonical(a) == fs::weakly_canonical(b)) {
        throw std::runtime_error("file paths must be distinct: " + a + " and " + b);
    }
}
} // namespace itch
