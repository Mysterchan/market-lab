#include "itch/output/staged_output.hpp"

#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fcntl.h>
#include <filesystem>
#include <stdexcept>
#include <string>
#include <unistd.h>
#include <vector>

namespace itch {

StagedOutput::StagedOutput(const std::string& destination, const std::string& input)
    : destination_(destination) {
    namespace fs = std::filesystem;
    std::error_code ec;
    if (fs::exists(destination_, ec) && fs::equivalent(input, destination_, ec) && !ec) {
        throw std::runtime_error("input and output refer to the same file");
    }

    const fs::path target(destination_);
    const fs::path parent = target.has_parent_path() ? target.parent_path() : fs::path(".");
    const fs::path pattern = parent / ("." + target.filename().string() + ".partial.XXXXXX");
    std::string name = pattern.string();
    std::vector<char> buffer(name.begin(), name.end());
    buffer.push_back('\0');
    const int descriptor = mkstemp(buffer.data());
    if (descriptor < 0) {
        throw std::runtime_error("cannot create temporary output beside " + destination_ + ": " +
                                 std::strerror(errno));
    }
    temporary_ = buffer.data();
    if (close(descriptor) != 0) {
        const int error = errno;
        std::remove(temporary_.c_str());
        throw std::runtime_error("cannot close temporary output: " +
                                 std::string(std::strerror(error)));
    }
    stream_.open(temporary_, std::ios::binary | std::ios::out | std::ios::trunc);
    if (!stream_) {
        std::remove(temporary_.c_str());
        throw std::runtime_error("cannot open temporary output beside " + destination_);
    }
}

StagedOutput::~StagedOutput() {
    if (stream_.is_open()) {
        stream_.close();
    }
    if (!committed_ && !preserved_) {
        std::remove(temporary_.c_str());
    }
}

void StagedOutput::sync() {
    stream_.flush();
    if (!stream_) {
        throw std::runtime_error("output write failed");
    }
    const int fd = open(temporary_.c_str(), O_RDONLY);
    if (fd < 0) {
        throw std::runtime_error("cannot open temporary output for sync");
    }
    const int synced = fsync(fd);
    const int closed = close(fd);
    if (synced != 0 || closed != 0) {
        throw std::runtime_error("temporary output sync failed");
    }
}

void StagedOutput::commit(const std::function<void(const std::string&)>& validate) {
    stream_.flush();
    if (!stream_) {
        throw std::runtime_error("output write failed");
    }
    stream_.close();
    if (!stream_) {
        throw std::runtime_error("output close failed");
    }
    if (validate) {
        const int fd = open(temporary_.c_str(), O_RDONLY);
        if (fd < 0) {
            throw std::runtime_error("cannot open temporary output for sync");
        }
        const int synced = fsync(fd);
        const int closed = close(fd);
        if (synced != 0 || closed != 0) {
            throw std::runtime_error("temporary output sync failed");
        }
        validate(temporary_);
    }
    if (std::rename(temporary_.c_str(), destination_.c_str()) != 0) {
        throw std::runtime_error("cannot publish output " + destination_ + ": " +
                                 std::strerror(errno));
    }
    committed_ = true;
}

} // namespace itch
