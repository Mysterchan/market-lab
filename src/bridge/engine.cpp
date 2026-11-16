#include "engine.hpp"
#include "itch/common/binary.hpp"
#include "itch/feed/itch/decoder.hpp"
#include <stdexcept>

namespace {
std::string stock(const itch::Bytes& b, size_t offset) {
    std::string s(b.begin() + offset, b.begin() + offset + 8);
    while (!s.empty() && s.back() == ' ') {
        s.pop_back();
    }
    return s;
}

bool selected_symbol(const itch::bridge::Engine& e, const itch::Bytes& b, char type) {
    auto locate = static_cast<uint16_t>(itch::read_big_endian(b.data() + 1, 2));
    bool selected = e.locate && locate == *e.locate;
    if (type == 'R' && stock(b, 11) == e.symbol) {
        selected = true;
    }
    if ((type == 'A' || type == 'F') && stock(b, 24) == e.symbol) {
        selected = true;
    }
    if ((type == 'P' && stock(b, 24) == e.symbol) || (type == 'Q' && stock(b, 19) == e.symbol) ||
        (type == 'H' && stock(b, 11) == e.symbol)) {
        if (!selected) {
            throw std::runtime_error("trade before directory or locate mismatch");
        }
    } else if (type == 'P' || type == 'Q' || type == 'H') {
        if (selected) {
            throw std::runtime_error("trade symbol/locate mismatch");
        }
    }
    return selected;
}

void trade_fields(itch::bridge::Applied& result, const itch::Bytes& b,
                  const std::optional<itch::LiveOrder>& old) {
    const bool selected = result.selected;
    const char type = result.type;
    auto read = [&](size_t offset, size_t width) {
        return itch::read_big_endian(b.data() + offset, width);
    };
    if (selected && (type == 'E' || type == 'C' || type == 'P' || type == 'Q')) {
        if (type == 'C' && b[31] != 'Y' && b[31] != 'N') {
            throw std::runtime_error("invalid printable flag");
        }
        const auto shares = type == 'Q' ? read(11, 8) : read(type == 'P' ? 20 : 19, 4);
        const auto price = type == 'E' ? old->price : read(type == 'Q' ? 27 : 32, 4);
        const auto match = read(type == 'Q' ? 31 : (type == 'P' ? 36 : 23), 8);
        if ((shares == 0 && type != 'Q') || (shares && !price)) {
            throw std::runtime_error("invalid trade fields");
        }
        result.match = match;
        result.price = price;
        result.quantity = shares;
        result.flags = 1 | ((type != 'C' || b[31] == 'Y') ? 2 : 0);
    }
    if (selected && type == 'B') {
        result.flags |= 4;
        result.broken = read(11, 8);
    }
}

void publication_fields(itch::bridge::Applied& result, const itch::Bytes& b) {
    const bool selected = result.selected;
    const char type = result.type;
    if (selected && type == 'H') {
        if (b[19] != 'H' && b[19] != 'P' && b[19] != 'Q' && b[19] != 'T') {
            throw std::runtime_error("invalid stock trading state");
        }
        result.trading = static_cast<char>(b[19]);
    }
    if (type == 'S') {
        if (b[11] != 'O' && b[11] != 'S' && b[11] != 'Q' && b[11] != 'M' && b[11] != 'E' &&
            b[11] != 'C') {
            throw std::runtime_error("invalid system event");
        }
        result.system = static_cast<char>(b[11]);
    }
}

} // namespace

namespace itch::bridge {
Applied apply(Engine& e, const Bytes& b, uint64_t frame, size_t depth) {
    auto event = itch::decode(b, frame); // strict length validation before field access
    const char type = static_cast<char>(b[0]);
    const bool selected = selected_symbol(e, b, type);
    auto old = (selected && (type == 'E' || type == 'C'))
                   ? e.book.order(itch::read_big_endian(b.data() + 11, 8))
                   : std::nullopt;
    Applied result;
    result.book = event ? e.book.apply(*event, depth, false) : std::nullopt;
    if (selected && (type == 'R' || type == 'A' || type == 'F')) {
        e.locate = static_cast<uint16_t>(itch::read_big_endian(b.data() + 1, 2));
    }
    result.timestamp = itch::read_big_endian(b.data() + 5, 6);
    result.type = type;
    result.selected = selected;
    trade_fields(result, b, old);
    publication_fields(result, b);
    return result;
}
} // namespace itch::bridge
