#include "itch/output/csv.hpp"

#include <iomanip>
#include <sstream>

namespace itch {
namespace {

std::string price(uint32_t raw_price) {
    std::ostringstream out;
    out << raw_price / 10000 << '.' << std::setfill('0') << std::setw(4) << raw_price % 10000;
    return out.str();
}

std::string depth(const std::vector<Level>& levels) {
    std::ostringstream out;
    for (size_t i = 0; i < levels.size(); ++i) {
        if (i) {
            out << '|';
        }
        out << price(levels[i].price) << ':' << levels[i].shares;
    }
    return out.str();
}

} // namespace

const char* csv_header(bool with_offset) {
    return with_offset ? "frame,timestamp_ns,type,bid,bid_shares,ask,ask_shares,bid_levels,ask_"
                         "levels,bid_depth,ask_depth,byte_offset,byte_end_offset"
                       : "frame,timestamp_ns,type,bid,bid_shares,ask,ask_shares,bid_levels,ask_"
                         "levels,bid_depth,ask_depth";
}

std::string csv_line(const Snapshot& snapshot, bool with_offset) {
    std::ostringstream out;
    out << snapshot.frame << ',' << snapshot.timestamp << ',' << snapshot.type << ',';
    if (snapshot.bids.empty()) {
        out << ',';
    } else {
        out << price(snapshot.bids.front().price) << ',' << snapshot.bids.front().shares;
    }
    out << ',';
    if (snapshot.asks.empty()) {
        out << ',';
    } else {
        out << price(snapshot.asks.front().price) << ',' << snapshot.asks.front().shares;
    }
    out << ',' << snapshot.bid_levels << ',' << snapshot.ask_levels << ',' << depth(snapshot.bids)
        << ',' << depth(snapshot.asks);
    if (with_offset) {
        out << ',' << snapshot.byte_offset << ',' << snapshot.byte_end_offset;
    }
    return out.str();
}

} // namespace itch
