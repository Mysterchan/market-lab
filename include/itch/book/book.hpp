#pragma once

#include "itch/feed/itch/event.hpp"

#include <cstddef>
#include <cstdint>
#include <map>
#include <optional>
#include <string>
#include <unordered_map>
#include <vector>

namespace itch {

struct Level {
    uint32_t price;
    uint64_t shares;
};

struct LiveOrder {
    uint64_t id;
    char side;
    uint32_t shares, price;
};

struct BookState {
    std::optional<uint16_t> locate;
    std::vector<LiveOrder> orders;
    std::vector<Level> bids, asks;
};

struct Snapshot {
    uint64_t frame, timestamp;
    char type;
    std::vector<Level> bids, asks;
    size_t bid_levels, ask_levels;
    uint64_t byte_offset = 0;
    uint64_t byte_end_offset = 0;
};

/** Reconstruct the displayed order book for one symbol from ordered ITCH events.
 * Instances are not thread-safe. After apply() fails, discard the instance rather than continue.
 */
class Book {
  public:
    explicit Book(std::string target);
    /** Apply one event; nullopt means directory-only or a different symbol.
     * max_depth limits the snapshot, not the stored price levels.
     * check_book enables a full order-to-level invariant check after each selected event.
     * Throws std::runtime_error on invalid order operations or inconsistent symbol/locate data.
     */
    std::optional<Snapshot> apply(const Event& event, size_t max_depth, bool check_book);
    /** Export deterministic state: orders sorted by ID and levels by ascending price. */
    BookState state() const;
    /** Inspect an existing order without copying the book (market trade adapter). */
    std::optional<LiveOrder> order(uint64_t id) const;
    /** Validate a saved state and replace the book only after all checks succeed.
     * frame identifies the checkpoint boundary in diagnostics; throws std::runtime_error on
     * failure.
     */
    void restore(const BookState& state, uint64_t frame);

  private:
    struct Order {
        char side;
        uint32_t shares;
        uint32_t price;
    };

    [[noreturn]] static void fail(uint64_t frame, const char* reason);
    void apply_directory(const Event& event);
    // Returns false for another symbol; selected adds update orders and levels together.
    bool add_order(const Event& event);
    // Reduce, delete or replace an existing order after locate selection.
    void change_order(const Event& event);
    void change_level(char side, uint32_t price, uint32_t shares, bool add, uint64_t frame);
    void verify_levels(uint64_t frame) const;
    Snapshot snapshot(const Event& event, size_t max_depth) const;

    std::string target_;
    std::optional<uint16_t> locate_;
    std::unordered_map<uint64_t, Order> orders_;
    std::map<uint32_t, uint64_t> bids_, asks_;
};

} // namespace itch
