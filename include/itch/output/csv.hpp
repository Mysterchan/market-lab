#pragma once

#include "itch/book/book.hpp"

#include <string>

namespace itch {

/** Return the CSV header without a newline; optional offsets describe BinaryFILE byte ranges. */
const char* csv_header(bool with_offset = false);
/** Format a book snapshot without a newline; prices use four decimal places. */
std::string csv_line(const Snapshot& snapshot, bool with_offset = false);

} // namespace itch
