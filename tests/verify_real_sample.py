"""Independently check an ITCH replay CSV against the original BinaryFILE feed.

Usage: python3 tests/verify_real_sample.py INPUT.bin OUTPUT.csv --symbol AAPL --depth 5
The script checks every CSV row's event identity and rebuilds book depth at
deterministically selected rows from its own live-order table.
"""

import argparse
import csv
import mmap
import random
import struct
from collections import Counter
from pathlib import Path


HEADER = [
    "frame", "timestamp_ns", "type", "bid", "bid_shares", "ask",
    "ask_shares", "bid_levels", "ask_levels", "bid_depth", "ask_depth",
]
ORDER_TYPES = b"AFECXDU"


def number(feed, offset, size):
    return int.from_bytes(feed[offset:offset + size], "big")


def price(raw):
    return f"{raw // 10000}.{raw % 10000:04d}"


def expected_book(orders, depth):
    totals = {ord("B"): {}, ord("S"): {}}
    for side, shares, raw_price in orders.values():
        levels = totals[side]
        levels[raw_price] = levels.get(raw_price, 0) + shares
    bids = sorted(totals[ord("B")].items(), reverse=True)
    asks = sorted(totals[ord("S")].items())

    def top(levels):
        return (price(levels[0][0]), str(levels[0][1])) if levels else ("", "")

    def levels_text(levels):
        return "|".join(f"{price(p)}:{shares}" for p, shares in levels[:depth])

    bid, bid_shares = top(bids)
    ask, ask_shares = top(asks)
    return [bid, bid_shares, ask, ask_shares, str(len(bids)), str(len(asks)),
            levels_text(bids), levels_text(asks)]


def sample_positions(row_count, count, seed):
    edge = min(25, row_count)
    selected = set(range(1, edge + 1))
    selected.update(range(max(1, row_count - edge + 1), row_count + 1))
    if row_count:
        selected.update(1 + (i * (row_count - 1)) // max(1, count - 1)
                        for i in range(count))
        selected.update(random.Random(seed).sample(range(1, row_count + 1),
                                                  min(count, row_count)))
    return selected


def check(input_path, csv_path, symbol, depth, samples, seed):
    with csv_path.open(newline="") as source:
        reader = csv.reader(source)
        if next(reader, None) != HEADER:
            raise ValueError("CSV header differs from the expected format")
        row_count = sum(1 for _ in reader)
    selected = sample_positions(row_count, samples, seed)
    expected_symbol = symbol.encode("ascii").ljust(8, b" ")
    orders = {}
    types = Counter()
    sample_types = Counter()
    target_locate = None
    rows_checked = 0
    samples_checked = 0
    frame = 0

    with input_path.open("rb") as binary, csv_path.open(newline="") as source:
        feed = mmap.mmap(binary.fileno(), 0, access=mmap.ACCESS_READ)
        reader = csv.reader(source)
        next(reader)
        offset = 0
        end = len(feed)
        while offset < end:
            if offset + 2 > end:
                raise ValueError(f"truncated length after frame {frame}")
            length = struct.unpack_from(">H", feed, offset)[0]
            offset += 2
            if length == 0:
                if offset != end:
                    raise ValueError("bytes follow the BinaryFILE end marker")
                break
            frame += 1
            if offset + length > end:
                raise ValueError(f"truncated payload at frame {frame}")
            kind = feed[offset]
            locate = struct.unpack_from(">H", feed, offset + 1)[0]
            if kind == ord("R") and feed[offset + 11:offset + 19] == expected_symbol:
                if target_locate is not None and target_locate != locate:
                    raise ValueError(f"AAPL locate changed at frame {frame}")
                target_locate = locate
            elif kind in ORDER_TYPES and locate == target_locate:
                if kind in (ord("A"), ord("F")):
                    if feed[offset + 24:offset + 32] != expected_symbol:
                        raise ValueError(f"symbol/locate mismatch at frame {frame}")
                    order_id = number(feed, offset + 11, 8)
                    if order_id in orders:
                        raise ValueError(f"duplicate order at frame {frame}")
                    orders[order_id] = (feed[offset + 19],
                                        number(feed, offset + 20, 4),
                                        number(feed, offset + 32, 4))
                elif kind == ord("U"):
                    old_id = number(feed, offset + 11, 8)
                    new_id = number(feed, offset + 19, 8)
                    side, _, _ = orders.pop(old_id)
                    if new_id in orders:
                        raise ValueError(f"duplicate replacement at frame {frame}")
                    orders[new_id] = (side, number(feed, offset + 27, 4),
                                      number(feed, offset + 31, 4))
                else:
                    order_id = number(feed, offset + 11, 8)
                    side, shares, raw_price = orders[order_id]
                    if kind == ord("D"):
                        del orders[order_id]
                    else:
                        removed = number(feed, offset + 19, 4)
                        if not 0 < removed <= shares:
                            raise ValueError(f"invalid reduction at frame {frame}")
                        if removed == shares:
                            del orders[order_id]
                        else:
                            orders[order_id] = (side, shares - removed, raw_price)

                rows_checked += 1
                row = next(reader, None)
                if row is None or len(row) != len(HEADER):
                    raise ValueError(f"missing/malformed CSV row {rows_checked}")
                identity = [str(frame), str(number(feed, offset + 5, 6)), chr(kind)]
                if row[:3] != identity:
                    raise ValueError(f"CSV event mismatch at row {rows_checked}: "
                                     f"{row[:3]} != {identity}")
                types[chr(kind)] += 1
                # Rare event types should be represented even when the random
                # and evenly spaced positions do not happen to hit them.
                if rows_checked in selected or sample_types[chr(kind)] < 5:
                    expected = expected_book(orders, depth)
                    if row[3:] != expected:
                        mismatches = [(HEADER[i + 3], row[i + 3], value)
                                      for i, value in enumerate(expected)
                                      if row[i + 3] != value]
                        raise ValueError(f"book mismatch at row {rows_checked}, frame "
                                         f"{frame}: {mismatches}")
                    samples_checked += 1
                    sample_types[chr(kind)] += 1
            offset += length
        if next(reader, None) is not None or rows_checked != row_count:
            raise ValueError("CSV has extra rows or row count changed")
        feed.close()

    print(f"PASS: {frame:,} frames; {rows_checked:,} CSV identities checked; "
          f"{samples_checked:,} full-book samples matched; "
          f"{len(orders):,} live orders at end")
    print("Event counts:", dict(sorted(types.items())))
    print("Sample event counts:", dict(sorted(sample_types.items())))
    print(f"Sampling: first/last 25 rows, first 5 of each event type, "
          f"{samples} evenly spread rows, {samples} random rows (seed {seed})")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("csv", type=Path)
    parser.add_argument("--symbol", default="AAPL")
    parser.add_argument("--depth", type=int, default=5)
    parser.add_argument("--samples", type=int, default=200)
    parser.add_argument("--seed", type=int, default=20260926)
    args = parser.parse_args()
    if args.depth < 1 or args.samples < 1:
        parser.error("--depth and --samples must be positive")
    check(args.input, args.csv, args.symbol, args.depth, args.samples, args.seed)
