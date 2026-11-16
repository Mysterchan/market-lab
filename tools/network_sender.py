#!/usr/bin/env python3
"""Bounded local MoldUDP64 unicast simulator. Synthetic data; no Nasdaq connection."""
import argparse
import socket
import struct
import time
from pathlib import Path


def endpoint(text):
    host, port = text.rsplit(":", 1)
    socket.inet_aton(host)
    port = int(port)
    if not 1 <= port <= 65535:
        raise ValueError("invalid port")
    return host, port


def synthetic():
    def common(kind, stamp):
        return kind + struct.pack(">HH", 7, 0) + stamp.to_bytes(6, "big")

    name = b"TEST    "
    directory = (
        common(b"R", 1)
        + name
        + b"QN"
        + struct.pack(">I", 100)
        + b"NC  PNN1N"
        + struct.pack(">I", 1)
        + b"N"
    )

    def add(order, shares, stamp):
        return (
            common(b"A", stamp)
            + struct.pack(">QcI", order, b"B", shares)
            + name
            + struct.pack(">I", 1000000)
        )

    return [
        directory,
        add(1, 100, 2),
        add(2, 200, 3),
        common(b"X", 4) + struct.pack(">QI", 1, 20),
        common(b"D", 5) + struct.pack(">Q", 2),
        common(b"D", 6) + struct.pack(">Q", 1),
    ]


def packet(session, sequence, messages=(), count=None):
    body = b"".join(struct.pack(">H", len(m)) + m for m in messages)
    wire = session + struct.pack(">QH", sequence, len(messages) if count is None else count) + body
    if len(wire) > 65507:
        raise ValueError("datagram exceeds IPv4 UDP payload limit")
    return wire


def load(path, limit):
    if path is None:
        return synthetic()
    messages = []
    with Path(path).open("rb") as feed:
        while True:
            prefix = feed.read(2)
            if len(prefix) != 2:
                raise ValueError("fixture needs a BinaryFILE end marker")
            length = int.from_bytes(prefix, "big")
            if not length:
                if feed.read(1):
                    raise ValueError("bytes after end marker")
                break
            if len(messages) >= limit:
                raise ValueError("fixture exceeds --max-frames; use a finite prefix fixture")
            payload = feed.read(length)
            if len(payload) != length:
                raise ValueError("truncated fixture")
            if length + 22 > 65507:
                raise ValueError("fixture message too large for UDP")
            messages.append(payload)
    return messages


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", default="127.0.0.1:19000")
    parser.add_argument("--source", default="127.0.0.1:19001")
    parser.add_argument("--recovery", default="127.0.0.1:19002")
    parser.add_argument("--session", default="SIM0000001")
    parser.add_argument("--input")
    parser.add_argument(
        "--write-fixture", help="write the same messages as BinaryFILE for offline comparison"
    )
    parser.add_argument(
        "--scenario", choices=["clean", "recover", "no-recovery"], default="recover"
    )
    parser.add_argument("--max-frames", type=int, default=100000)
    parser.add_argument("--serve-seconds", type=float, default=5)
    args = parser.parse_args()
    session = args.session.encode("ascii")
    if len(session) != 10 or args.max_frames <= 0 or not 0 < args.serve_seconds <= 3600:
        parser.error("session must be 10 ASCII bytes, limits must be positive (serve <= 3600s)")
    destination = endpoint(args.destination)
    messages = load(args.input, args.max_frames)
    if args.write_fixture:
        if args.input and Path(args.input).resolve() == Path(args.write_fixture).resolve():
            parser.error("fixture output must differ from input")
        with Path(args.write_fixture).open("wb") as out:
            for message in messages:
                out.write(struct.pack(">H", len(message)) + message)
            out.write(b"\0\0")
    with (
        socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as source,
        socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as recovery,
    ):
        source.bind(endpoint(args.source))
        recovery.bind(endpoint(args.recovery))
        recovery.settimeout(0.05)
        print(
            f"simulator_ready session={args.session} messages={len(messages)} scenario={args.scenario}",
            flush=True,
        )
        order = list(range(1, len(messages) + 1))
        if args.scenario != "clean" and len(messages) >= 2:
            order = [1] + list(range(len(messages), 2, -1))  # omit 2 and reverse the tail
        for seq in order:
            source.sendto(packet(session, seq, [messages[seq - 1]]), destination)
            time.sleep(0.001)
        if order:
            source.sendto(packet(session, order[-1], [messages[order[-1] - 1]]), destination)
        end = packet(session, len(messages) + 1, count=65535)
        source.sendto(end, destination)
        deadline = time.monotonic() + args.serve_seconds
        next_end = time.monotonic() + 0.1
        requests = 0
        while time.monotonic() < deadline:
            if time.monotonic() >= next_end:
                source.sendto(end, destination)
                next_end = time.monotonic() + 0.1
            try:
                request, client = recovery.recvfrom(65536)
            except socket.timeout:
                continue
            if len(request) != 20 or request[:10] != session or client != destination:
                continue
            first, count = struct.unpack(">QH", request[10:])
            if not count or first < 1 or first > len(messages):
                continue
            requests += 1
            print(f"request sequence={first} count={count}", flush=True)
            if args.scenario == "no-recovery":
                continue
            # Deliberately partial responses exercise repeated range requests.
            for seq in range(first, min(first + count, first + 2, len(messages) + 1)):
                recovery.sendto(packet(session, seq, [messages[seq - 1]]), client)
        print(f"simulator_complete requests={requests}", flush=True)


if __name__ == "__main__":
    main()
