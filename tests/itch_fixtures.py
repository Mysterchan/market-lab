"""Synthetic ITCH frames and an independent order-book reference model."""

import struct

def common(kind, locate=7, timestamp=1):
    return kind.encode() + struct.pack(">HH", locate, 0) + timestamp.to_bytes(6, "big")


def stock(name):
    return name.encode().ljust(8, b" ")


def frame(payload):
    return struct.pack(">H", len(payload)) + payload


def directory(name="TEST", locate=7, timestamp=1):
    return frame(common("R", locate, timestamp) + stock(name) + b"Q" + b"N" +
                 struct.pack(">I", 100) + b"N" + b"C" + b"  " + b"P" +
                 b"N" + b"N" + b"1" + b"N" + struct.pack(">I", 1) + b"N")


def add(order, side, shares, price, name="TEST", kind="A", locate=7, timestamp=2):
    payload = (common(kind, locate, timestamp) + struct.pack(">Q", order) +
               side.encode() + struct.pack(">I", shares) + stock(name) +
               struct.pack(">I", price))
    if kind == "F":
        payload += b"ABCD"
    return frame(payload)


def reduce(kind, order, shares, locate=7, timestamp=3, match=123):
    payload = common(kind, locate, timestamp) + struct.pack(">QI", order, shares)
    if kind == "E":
        payload += struct.pack(">Q", match)
    elif kind == "C":
        payload += struct.pack(">Q", match) + b"Y" + struct.pack(">I", 999900)
    return frame(payload)


def delete(order, timestamp=4):
    return frame(common("D", 7, timestamp) + struct.pack(">Q", order))


def replace(old, new, shares, price, timestamp=5):
    return frame(common("U", 7, timestamp) + struct.pack(">QQII", old, new, shares, price))


def reference_row(orders, frame_number, timestamp, kind, depth_limit):
    """Rebuild price totals from live orders instead of maintaining price maps."""
    sides = {"B": {}, "S": {}}
    for side, shares, raw_price in orders.values():
        levels = sides[side]
        levels[raw_price] = levels.get(raw_price, 0) + shares
    bids = sorted(sides["B"].items(), reverse=True)
    asks = sorted(sides["S"].items())

    def formatted(raw_price):
        return f"{raw_price // 10000}.{raw_price % 10000:04d}"

    def depth_text(levels):
        return "|".join(f"{formatted(p)}:{shares}" for p, shares in levels[:depth_limit])

    return {
        "frame": str(frame_number), "timestamp_ns": str(timestamp), "type": kind,
        "bid": formatted(bids[0][0]) if bids else "",
        "bid_shares": str(bids[0][1]) if bids else "",
        "ask": formatted(asks[0][0]) if asks else "",
        "ask_shares": str(asks[0][1]) if asks else "",
        "bid_levels": str(len(bids)), "ask_levels": str(len(asks)),
        "bid_depth": depth_text(bids), "ask_depth": depth_text(asks),
    }

