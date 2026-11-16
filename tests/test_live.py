"""Actual loopback UDP integration and independent per-event book checks."""
import csv
from concurrent.futures import ThreadPoolExecutor
import io
import random
import socket
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from itch_fixtures import directory, add, reduce, delete, reference_row

EXE = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path("build/itch_replay").resolve()
ROOT = Path(__file__).resolve().parents[1]
SESSION = b"SIM0000001"


def wire(seq, messages=(), count=None, session=SESSION):
    return session + struct.pack(">QH", seq, len(messages) if count is None else count) + b"".join(
        struct.pack(">H", len(m)) + m for m in messages)


def bound():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(("127.0.0.1", 0))
    except Exception:
        sock.close()
        raise
    return sock


def endpoint(sock):
    host, port = sock.getsockname()
    return f"{host}:{port}"


class LiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source, self.recovery = bound(), bound()
        self.recovery.settimeout(1)
        self.address = None
        self.proc = None
        self.frames = [directory(), add(1, "B", 100, 1000000, timestamp=2),
                       add(2, "B", 200, 1000000, timestamp=3),
                       reduce("X", 1, 20, timestamp=4), delete(2, timestamp=5), delete(1, timestamp=6)]
        self.messages = [frame[2:] for frame in self.frames]
        self.output = self.root / "live.csv"
        self.output.write_text("OLD")
        self.logfile = self.root / "status.log"

    def tearDown(self):
        if self.proc and self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait()
        self.source.close()
        self.recovery.close()
        self.temp.cleanup()

    def start(self, *extra):
        reservation = bound()
        self.address = reservation.getsockname()
        listen = endpoint(reservation)
        reservation.close()
        args = [str(EXE), "--live", "--listen", listen, "--source", endpoint(self.source),
                "--recovery", endpoint(self.recovery), "--session", SESSION.decode(),
                "--symbol", "TEST", "--output", str(self.output), "--check-book",
                "--idle-timeout-ms", "700", "--recovery-timeout-ms", "350", "--retry-ms", "30", *extra]
        with self.logfile.open("w") as log:
            self.proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=log)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if "listening=" in self.logfile.read_text():
                return
            if self.proc.poll() is not None:
                self.fail(self.logfile.read_text())
            time.sleep(0.005)
        self.fail("receiver did not become ready")

    def send(self, seq, messages=(), count=None, session=SESSION, recovery=False):
        (self.recovery if recovery else self.source).sendto(wire(seq, messages, count, session), self.address)

    def finish(self, success=True):
        self.proc.communicate(timeout=3)
        log = self.logfile.read_text()
        self.assertEqual(self.proc.returncode, 0 if success else 1, log)
        if not success:
            self.assertEqual(self.output.read_text(), "OLD", log)
            self.assertFalse(list(self.root.glob(".live.csv.partial.*")))
        return log

    def check_book(self):
        expected = []
        orders = {}
        for seq, timestamp, kind in [(2, 2, "A"), (3, 3, "A"), (4, 4, "X"), (5, 5, "D"), (6, 6, "D")]:
            if seq == 2:
                orders[1] = ("B", 100, 1000000)
            elif seq == 3:
                orders[2] = ("B", 200, 1000000)
            elif seq == 4:
                orders[1] = ("B", 80, 1000000)
            elif seq == 5:
                del orders[2]
            else:
                del orders[1]
            expected.append(reference_row(orders, seq, timestamp, kind, 5))
        self.assertEqual(list(csv.DictReader(io.StringIO(self.output.read_text()))), expected)
        feed = self.root / "fixture.bin"
        feed.write_bytes(b"".join(self.frames) + b"\0\0")
        baseline = subprocess.run([str(EXE), "--input", str(feed), "--symbol", "TEST", "--verify", "--check-book"],
                                  capture_output=True, check=True)
        self.assertEqual(self.output.read_bytes(), baseline.stdout)

    def request(self):
        data, client = self.recovery.recvfrom(65536)
        self.assertEqual(data[:10], SESSION)
        self.assertEqual(len(data), 20)
        self.assertEqual(client, self.address)
        return struct.unpack(">QH", data[10:])

    def test_clean_batched_and_duplicate(self):
        self.start()
        self.send(1, self.messages[:3])
        self.send(2, self.messages[1:4])  # overlap processed plus new message
        self.send(5, self.messages[4:])
        self.send(7, count=65535)
        log = self.finish()
        self.assertIn("duplicates=2", log)
        self.check_book()

    def test_gap_out_of_order_duplicate_and_recovery(self):
        self.start()
        self.send(1, self.messages[:1])
        self.send(5, self.messages[4:])
        self.send(3, self.messages[2:4])
        self.send(3, self.messages[2:4])
        self.send(7, count=65535)
        seq, count = self.request()
        self.assertEqual(seq, 2)
        self.assertGreaterEqual(count, 1)
        self.send(2, self.messages[1:2], recovery=True)
        log = self.finish()
        self.assertIn("state=RECOVERING", log)
        self.assertIn("state=SYNCED", log)
        self.assertIn("nasdaq_connection=unverified", log)
        self.check_book()

    def test_lost_first_message_and_partial_recovery(self):
        self.start()
        self.send(6, self.messages[5:])
        self.send(7, count=65535)
        first, count = self.request()
        self.assertEqual((first, count), (1, 5))
        self.send(1, self.messages[:2], recovery=True)
        first, count = self.request()
        self.assertEqual((first, count), (3, 3))
        self.send(3, self.messages[2:5], recovery=True)
        self.finish()
        self.check_book()

    def test_heartbeat_detects_missing_tail(self):
        self.start()
        self.send(1, self.messages[:4])
        self.send(7)  # heartbeat announces sender's next sequence
        self.assertEqual(self.request(), (5, 2))
        self.send(5, self.messages[4:], recovery=True)
        self.send(7, count=65535)
        self.finish()
        self.check_book()

    def test_end_boundary_recovers_missing_tail(self):
        self.start()
        self.send(1, self.messages[:4])
        self.send(7, count=65535)
        self.assertEqual(self.request(), (5, 2))
        self.send(5, self.messages[4:], recovery=True)
        self.finish()
        self.check_book()

    def test_missing_end_is_not_success(self):
        self.start()
        self.send(1, self.messages)
        self.assertIn("progress timeout", self.finish(False))

    def test_silent_socket_timeout(self):
        self.start()
        self.assertIn("progress timeout", self.finish(False))

    def test_no_recovery_retries_and_fails(self):
        self.start()
        self.send(1, self.messages[:1])
        self.send(3, self.messages[2:])
        self.send(7, count=65535)
        self.assertEqual(self.request(), (2, 1))
        self.assertEqual(self.request(), (2, 1))
        self.assertIn("recovery timeout", self.finish(False))

    def test_session_mismatch(self):
        self.start()
        self.send(1, self.messages[:1], session=b"OTHER00001")
        self.assertIn("session mismatch", self.finish(False))

    def test_buffer_message_limit(self):
        self.start("--max-buffer-messages", "2")
        self.send(3, self.messages[2:])
        self.assertIn("buffer limit", self.finish(False))

    def test_buffer_byte_limit(self):
        self.start("--max-buffer-bytes", "40")
        self.send(3, self.messages[2:4])
        self.assertIn("buffer limit", self.finish(False))

    def test_conflicting_buffered_duplicate(self):
        self.start()
        self.send(3, self.messages[2:3])
        changed = bytearray(self.messages[2]); changed[-1] ^= 1
        self.send(3, [bytes(changed)])
        self.assertIn("conflicting buffered duplicate", self.finish(False))

    def test_conflicting_processed_duplicate(self):
        self.start()
        self.send(1, self.messages[:2])
        changed = bytearray(self.messages[1]); changed[-1] ^= 1
        self.send(2, [bytes(changed)])
        self.assertIn("conflicting processed duplicate", self.finish(False))

    def test_unconfigured_source_ignored(self):
        self.start()
        with bound() as stranger:
            stranger.sendto(b"garbage", self.address)
        self.send(1, self.messages)
        self.send(7, count=65535)
        self.assertIn("ignored_sources=1", self.finish())
        self.check_book()

    def test_malformed_datagrams(self):
        cases = [b"short", wire(1, self.messages[:1])[:-1], wire(1, count=1),
                 wire(1) + b"trailing", wire(1, [b"Z"]), wire(0), wire(2**64-1),
                 wire(2**64-2, self.messages[:2]), wire(1, count=65535) + b"x"]
        for data in cases:
            with self.subTest(data=data):
                self.start()
                self.source.sendto(data, self.address)
                self.finish(False)

    def test_invalid_book_event_fails(self):
        self.start()
        self.send(1, [self.messages[0], delete(999)[2:]])
        self.assertIn("unknown order", self.finish(False))

    def test_inconsistent_end_boundary(self):
        self.start()
        self.send(3, self.messages[2:3])
        self.send(2, count=65535)
        self.assertIn("inconsistent end", self.finish(False))

    def test_data_beyond_declared_end(self):
        self.start()
        self.send(4, count=65535)  # pending recovery, not complete
        self.send(4, self.messages[3:4])
        self.assertIn("beyond end", self.finish(False))

    def test_empty_session(self):
        self.start()
        self.send(1, count=65535)
        self.assertIn("frames=0", self.finish())
        self.assertEqual(len(list(csv.DictReader(io.StringIO(self.output.read_text())))), 0)

    def test_sender_command(self):
        self.start()
        source = endpoint(self.source); recovery = endpoint(self.recovery)
        self.source.close(); self.recovery.close()
        fixture = self.root / "sender.bin"
        sender = subprocess.run([sys.executable, str(ROOT / "tools/network_sender.py"), "--destination",
            f"{self.address[0]}:{self.address[1]}", "--source", source, "--recovery", recovery,
            "--write-fixture", str(fixture), "--serve-seconds", "0.6"], capture_output=True, text=True, timeout=3)
        self.assertEqual(sender.returncode, 0, sender.stderr)
        self.assertIn("request sequence=", sender.stdout)
        self.finish()
        self.check_book()
        self.assertEqual(fixture.read_bytes(), b"".join(self.frames) + b"\0\0")

    def test_many_events_random_order_and_repeated_recovery(self):
        self.frames = [directory()]
        orders = {}
        expected = []
        for order in range(1, 81):
            seq = len(self.frames) + 1
            side = "B" if order % 2 else "S"
            price = 1000000 + (order % 9) * 100
            shares = 10 + order
            self.frames.append(add(order, side, shares, price, timestamp=seq))
            orders[order] = (side, shares, price)
            expected.append(reference_row(orders, seq, seq, "A", 5))
        for order in range(1, 81):
            seq = len(self.frames) + 1
            self.frames.append(reduce("X", order, 5, timestamp=seq))
            side, shares, price = orders[order]
            orders[order] = (side, shares - 5, price)
            expected.append(reference_row(orders, seq, seq, "X", 5))
        for order in range(80, 0, -1):
            seq = len(self.frames) + 1
            self.frames.append(delete(order, timestamp=seq))
            del orders[order]
            expected.append(reference_row(orders, seq, seq, "D", 5))
        self.messages = [f[2:] for f in self.frames]
        # Two messages per retry can repair the entire prefix inside the
        # recovery budget, even if source delivery is slow or packets are lost.
        self.start("--recovery-timeout-ms", "1000", "--idle-timeout-ms", "1500", "--retry-ms", "5")
        sequence = list(range(1, len(self.messages) + 1))
        random.Random(20260930).shuffle(sequence)
        missing = {1, 31, 70, 111, 190, 241}

        def send_source():
            for seq in sequence:
                if seq not in missing:
                    self.send(seq, self.messages[seq-1:seq])
                    if seq % 19 == 0:
                        self.send(seq, self.messages[seq-1:seq])
                    time.sleep(0.0005)
            self.send(len(self.messages) + 1, count=65535)

        # Service recovery while source traffic is still being sent. A slow
        # sender must not accidentally withhold every response until timeout.
        with ThreadPoolExecutor(max_workers=1) as sender:
            sent = sender.submit(send_source)
            self.request()  # Deliberately drop only the first response.
            deadline = time.monotonic() + 2
            while self.proc.poll() is None and time.monotonic() < deadline:
                try:
                    first, count = self.request()
                except socket.timeout:
                    continue
                self.send(first, self.messages[first-1:first-1+min(count, 2)], recovery=True)
            sent.result(timeout=3)
        log = self.finish()
        self.assertGreater(log.count("recovery_request "), 1)
        self.assertEqual(list(csv.DictReader(io.StringIO(self.output.read_text()))), expected)
        feed = self.root / "long.bin"
        feed.write_bytes(b"".join(self.frames) + b"\0\0")
        baseline = subprocess.run([str(EXE), "--input", str(feed), "--symbol", "TEST", "--verify"],
                                  check=True, capture_output=True)
        self.assertEqual(self.output.read_bytes(), baseline.stdout)

    def test_gap_is_reported_before_any_final_publication(self):
        self.start()
        self.send(1, self.messages[:1])
        self.send(3, self.messages[2:])
        self.request()
        self.assertEqual(self.output.read_text(), "OLD")
        # Status records next=2: no later event has been applied while 2 is missing.
        self.assertIn("state=RECOVERING next_sequence=2", self.logfile.read_text())
        self.send(2, self.messages[1:2], recovery=True)
        self.send(7, count=65535)
        self.finish()

    def test_network_options_require_live(self):
        result = subprocess.run([str(EXE), "--input", "missing.bin", "--symbol", "TEST", "--retry-ms", "100"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("network options require --live", result.stderr)

    def test_cli_rejects_incompatible_and_invalid_options(self):
        for args in [["--input", "x"], ["--verify"], ["--resume", "x"], ["--with-offset"],
                     ["--checkpoint-out", "x"], ["--allow-implicit-eof"], ["--retry-ms", "0"],
                     ["--session", "short"], ["--listen", "224.0.0.1:1234"],
                     ["--listen", "127.0.0.1:99999"], ["--retry-ms", "99999999999999999999"]]:
            with self.subTest(args=args):
                command = [str(EXE), "--live", "--listen", "127.0.0.1:19000", "--source", "127.0.0.1:19001",
                           "--recovery", "127.0.0.1:19002", "--session", SESSION.decode(), "--symbol", "TEST", *args]
                result = subprocess.run(command, capture_output=True, timeout=2)
                self.assertEqual(result.returncode, 1, result.stderr)


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]])
