#include "itch/book/book.hpp"

#include <algorithm>
#include <limits>
#include <stdexcept>
#include <utility>

namespace itch {

Book::Book(std::string target) : target_(std::move(target)) {}

std::optional<LiveOrder> Book::order(uint64_t id) const {
    auto it = orders_.find(id);
    if (it == orders_.end()) {
        return std::nullopt;
    }
    return LiveOrder{id, it->second.side, it->second.shares, it->second.price};
}

BookState Book::state() const {
    BookState state{locate_, {}, {}, {}};
    for (const auto& entry : orders_) {
        state.orders.push_back(
            {entry.first, entry.second.side, entry.second.shares, entry.second.price});
    }
    std::sort(state.orders.begin(), state.orders.end(), [](const LiveOrder& a, const LiveOrder& b) {
        return a.id < b.id;
    });
    for (const auto& level : bids_) {
        state.bids.push_back({level.first, level.second});
    }
    for (const auto& level : asks_) {
        state.asks.push_back({level.first, level.second});
    }
    return state;
}

void Book::restore(const BookState& state, uint64_t frame) {
    Book restored(target_);
    restored.locate_ = state.locate;
    if (!state.locate && !state.orders.empty()) {
        fail(frame, "checkpoint orders without stock locate");
    }
    for (const auto& order : state.orders) {
        if ((order.side != 'B' && order.side != 'S') || !order.shares || !order.price) {
            fail(frame, "checkpoint invalid live order");
        }
        if (!restored.orders_.emplace(order.id, Order{order.side, order.shares, order.price})
                 .second) {
            fail(frame, "checkpoint duplicate order reference");
        }
        restored.change_level(order.side, order.price, order.shares, true, frame);
    }
    const auto computed = restored.state();
    auto equal = [](const std::vector<Level>& a, const std::vector<Level>& b) {
        return a.size() == b.size() &&
               std::equal(a.begin(), a.end(), b.begin(), [](const Level& x, const Level& y) {
                   return x.price == y.price && x.shares == y.shares;
               });
    };
    if (!equal(computed.bids, state.bids) || !equal(computed.asks, state.asks)) {
        fail(frame, "checkpoint price levels differ from live orders");
    }
    restored.verify_levels(frame);
    *this = std::move(restored);
}

void Book::apply_directory(const Event& event) {
    const auto& directory = std::get<Directory>(event.data);
    if (directory.symbol == target_) {
        if (locate_ && *locate_ != event.locate) {
            fail(event.frame, "symbol changed stock locate");
        }
        locate_ = event.locate;
    } else if (locate_ && *locate_ == event.locate) {
        fail(event.frame, "stock locate belongs to another symbol");
    }
}

bool Book::add_order(const Event& event) {
    const auto& add = std::get<Add>(event.data);
    if (add.symbol != target_) {
        if (locate_ && *locate_ == event.locate) {
            fail(event.frame, "add symbol/locate mismatch");
        }
        return false;
    }
    if (locate_ && *locate_ != event.locate) {
        fail(event.frame, "add symbol/locate mismatch");
    }
    locate_ = event.locate;
    if ((add.side != 'B' && add.side != 'S') || add.shares == 0 || add.price == 0) {
        fail(event.frame, "invalid add fields");
    }
    if (!orders_.emplace(add.id, Order{add.side, add.shares, add.price}).second) {
        fail(event.frame, "duplicate order reference");
    }
    change_level(add.side, add.price, add.shares, true, event.frame);
    return true;
}

void Book::change_order(const Event& event) {
    uint64_t old_id = 0;
    if (event.type == 'E' || event.type == 'C' || event.type == 'X') {
        old_id = std::get<Reduce>(event.data).id;
    } else if (event.type == 'D') {
        old_id = std::get<Delete>(event.data).id;
    } else {
        old_id = std::get<Replace>(event.data).old_id;
    }

    auto it = orders_.find(old_id);
    if (it == orders_.end()) {
        fail(event.frame, "unknown order reference (possible missing/out-of-order event)");
    }
    const Order old = it->second;

    if (event.type == 'E' || event.type == 'C' || event.type == 'X') {
        const uint32_t removed = std::get<Reduce>(event.data).shares;
        if (removed == 0 || removed > old.shares) {
            fail(event.frame, "invalid share reduction");
        }
        change_level(old.side, old.price, removed, false, event.frame);
        if (removed == old.shares) {
            orders_.erase(it);
        } else {
            it->second.shares -= removed;
        }
    } else if (event.type == 'D') {
        change_level(old.side, old.price, old.shares, false, event.frame);
        orders_.erase(it);
    } else {
        const auto& replacement = std::get<Replace>(event.data);
        if (replacement.shares == 0 || replacement.price == 0 || replacement.new_id == old_id ||
            orders_.count(replacement.new_id)) {
            fail(event.frame, "invalid replacement");
        }
        change_level(old.side, old.price, old.shares, false, event.frame);
        orders_.erase(it);
        orders_.emplace(replacement.new_id, Order{old.side, replacement.shares, replacement.price});
        change_level(old.side, replacement.price, replacement.shares, true, event.frame);
    }
}

std::optional<Snapshot> Book::apply(const Event& event, size_t max_depth, bool check_book) {
    if (event.type == 'R') {
        apply_directory(event);
        return std::nullopt;
    }
    if (event.type == 'A' || event.type == 'F') {
        if (!add_order(event)) {
            return std::nullopt;
        }
    } else {
        if (!locate_ || event.locate != *locate_) {
            return std::nullopt;
        }
        change_order(event);
    }
    if (check_book) {
        verify_levels(event.frame);
    }
    return snapshot(event, max_depth);
}

[[noreturn]] void Book::fail(uint64_t frame, const char* reason) {
    throw std::runtime_error("frame " + std::to_string(frame) + ": " + reason);
}

void Book::change_level(char side, uint32_t price, uint32_t shares, bool add, uint64_t frame) {
    auto& levels = side == 'B' ? bids_ : asks_;
    const uint64_t current = levels.count(price) ? levels.at(price) : 0;
    if (add) {
        if (current > std::numeric_limits<uint64_t>::max() - shares) {
            fail(frame, "depth overflow");
        }
        levels[price] = current + shares;
    } else {
        if (current < shares) {
            fail(frame, "depth underflow");
        }
        if (current == shares) {
            levels.erase(price);
        } else {
            levels[price] = current - shares;
        }
    }
}

void Book::verify_levels(uint64_t frame) const {
    std::map<uint32_t, uint64_t> expected_bids, expected_asks;
    for (const auto& entry : orders_) {
        const Order& order = entry.second;
        if ((order.side != 'B' && order.side != 'S') || order.shares == 0 || order.price == 0) {
            fail(frame, "book invariant: invalid live order");
        }
        auto& expected = order.side == 'B' ? expected_bids : expected_asks;
        if (expected[order.price] > std::numeric_limits<uint64_t>::max() - order.shares) {
            fail(frame, "book invariant: depth overflow");
        }
        expected[order.price] += order.shares;
    }
    if (expected_bids != bids_ || expected_asks != asks_) {
        fail(frame, "book invariant: price levels differ from live orders");
    }
}

Snapshot Book::snapshot(const Event& event, size_t max_depth) const {
    Snapshot result{event.frame, event.timestamp, event.type, {}, {}, bids_.size(), asks_.size()};
    for (auto it = bids_.rbegin(); it != bids_.rend() && result.bids.size() < max_depth; ++it) {
        result.bids.push_back({it->first, it->second});
    }
    for (auto it = asks_.begin(); it != asks_.end() && result.asks.size() < max_depth; ++it) {
        result.asks.push_back({it->first, it->second});
    }
    return result;
}

} // namespace itch
