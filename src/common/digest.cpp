#include "itch/common/digest.hpp"
#include <array>
#include <fstream>
#include <iomanip>
#include <sstream>
#include <stdexcept>

namespace itch {
namespace {
// SHA-256, with explicit big-endian words and modulo-2^32 arithmetic.
class Sha256 {
    std::array<uint32_t, 8> h_{0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
                               0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19};
    std::array<unsigned char, 64> block_{};
    uint64_t size_ = 0;
    size_t used_ = 0;

    static uint32_t rotate(uint32_t x, unsigned n) {
        return (x >> n) | (x << (32 - n));
    }

    void compress() {
        static constexpr uint32_t k[] = {
            0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4,
            0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe,
            0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f,
            0x4a7484aa, 0x5cb0a9dc, 0x76f988da, 0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7,
            0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc,
            0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b,
            0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070, 0x19a4c116,
            0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
            0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7,
            0xc67178f2};
        uint32_t w[64];
        for (size_t i = 0; i < 16; ++i) {
            w[i] = 0;
            for (size_t j = 0; j < 4; ++j) {
                w[i] = (w[i] << 8) | block_[4 * i + j];
            }
        }
        for (size_t i = 16; i < 64; ++i) {
            const uint32_t a = w[i - 15], b = w[i - 2];
            w[i] = w[i - 16] + (rotate(a, 7) ^ rotate(a, 18) ^ (a >> 3)) + w[i - 7] +
                   (rotate(b, 17) ^ rotate(b, 19) ^ (b >> 10));
        }
        auto v = h_;
        for (size_t i = 0; i < 64; ++i) {
            const uint32_t t1 = v[7] + (rotate(v[4], 6) ^ rotate(v[4], 11) ^ rotate(v[4], 25)) +
                                ((v[4] & v[5]) ^ (~v[4] & v[6])) + k[i] + w[i];
            const uint32_t t2 = (rotate(v[0], 2) ^ rotate(v[0], 13) ^ rotate(v[0], 22)) +
                                ((v[0] & v[1]) ^ (v[0] & v[2]) ^ (v[1] & v[2]));
            for (size_t j = 7; j > 0; --j) {
                v[j] = v[j - 1];
            }
            v[4] += t1;
            v[0] = t1 + t2;
        }
        for (size_t i = 0; i < 8; ++i) {
            h_[i] += v[i];
        }
    }

  public:
    void update(const char* data, size_t length) {
        size_ += length;
        for (size_t i = 0; i < length; ++i) {
            block_[used_++] = static_cast<unsigned char>(data[i]);
            if (used_ == 64) {
                compress();
                used_ = 0;
            }
        }
    }

    std::string finish() {
        const uint64_t bits = size_ * 8;
        const char marker = static_cast<char>(0x80), zero = 0;
        update(&marker, 1);
        while (used_ != 56) {
            update(&zero, 1);
        }
        for (int i = 7; i >= 0; --i) {
            const char b = static_cast<char>(bits >> (i * 8));
            update(&b, 1);
        }
        std::ostringstream out;
        out << std::hex << std::setfill('0');
        for (uint32_t word : h_) {
            out << std::setw(8) << word;
        }
        return out.str();
    }
};
} // namespace

uint64_t fnv1a_bytes(uint64_t hash, std::string_view bytes) {
    for (unsigned char byte : bytes) {
        hash ^= byte;
        hash *= 1099511628211ULL;
    }
    return hash;
}

uint64_t hash_csv_line(uint64_t hash, std::string_view line) {
    return fnv1a_bytes(fnv1a_bytes(hash, line), "\n");
}

std::string sha256(const std::string& bytes) {
    Sha256 hash;
    hash.update(bytes.data(), bytes.size());
    return hash.finish();
}

Fingerprint fingerprint(const std::string& path) {
    std::ifstream in(path, std::ios::binary);
    if (!in) {
        throw std::runtime_error("cannot open input: " + path);
    }
    Sha256 hash;
    uint64_t size = 0;
    std::array<char, 65536> buffer;
    while (in.read(buffer.data(), buffer.size()) || in.gcount()) {
        const size_t count = static_cast<size_t>(in.gcount());
        hash.update(buffer.data(), count);
        size += count;
    }
    if (!in.eof() || in.bad()) {
        throw std::runtime_error("input digest read failed: " + path);
    }
    return {size, hash.finish()};
}
} // namespace itch
