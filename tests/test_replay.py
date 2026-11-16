"""Small, entirely synthetic BinaryFILE/ITCH 5.0 scenarios (not Nasdaq data)."""
import csv
import hashlib
import io
import random
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from itch_fixtures import common, frame, directory, add, reduce, delete, replace, reference_row

EXE = None
SKIPPED_LENGTHS = {
    "S": 12, "H": 25, "Y": 20, "L": 26, "V": 35, "W": 12,
    "K": 28, "J": 35, "h": 21, "P": 44, "Q": 40, "B": 19,
    "I": 50, "N": 20, "O": 48,
}




















class ReplayTests(unittest.TestCase):
    def run_feed(self, data, complete=True, extra_args=()):
        with tempfile.TemporaryDirectory() as directory_name:
            path = Path(directory_name) / "synthetic.bin"
            path.write_bytes(data + (b"\x00\x00" if complete else b""))
            return subprocess.run([str(EXE), "--input", str(path), "--symbol", "TEST", "--verify", *extra_args],
                                  text=True, capture_output=True)

    def test_cli_groups_allow_interleaving_and_last_value_wins(self):
        data = directory() + add(1, "B", 10, 1000000) + add(2, "B", 20, 999000)
        expected = self.run_feed(data, extra_args=("--depth", "1", "--with-offset", "--check-book"))
        self.assertEqual(expected.returncode, 0, expected.stderr)
        with tempfile.TemporaryDirectory() as directory_name:
            source = Path(directory_name) / "input.bin"
            output = Path(directory_name) / "output.csv"
            ignored = Path(directory_name) / "ignored.csv"
            source.write_bytes(data + b"\x00\x00")
            result = subprocess.run([
                str(EXE), "--depth", "2", "--input", "missing.bin", "--output", str(ignored),
                "--with-offset", "--symbol", "OTHER", "--check-book", "--input", str(source),
                "--verify", "--output", str(output), "--symbol", "TEST", "--depth", "1",
            ], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output.read_text(), expected.stdout)
            self.assertEqual(result.stdout, "")
            self.assertFalse(ignored.exists())
            self.assertIn("verified=yes", result.stderr)

    def test_cli_missing_values_and_unknown_options_fail_before_output(self):
        value_options = (
            "--input", "--symbol", "--output", "--depth", "--checkpoint-out", "--resume",
            "--checkpoint-every-frames", "--stop-after-frame", "--listen", "--source",
            "--recovery", "--session", "--idle-timeout-ms", "--recovery-timeout-ms",
            "--retry-ms", "--max-buffer-messages", "--max-buffer-bytes", "--unknown-option",
        )
        with tempfile.TemporaryDirectory() as directory_name:
            source = Path(directory_name) / "input.bin"
            output = Path(directory_name) / "output.csv"
            source.write_bytes(directory() + b"\x00\x00")
            output.write_text("previous result")
            for option in value_options:
                with self.subTest(option=option):
                    result = subprocess.run([
                        str(EXE), "--input", str(source), "--symbol", "TEST",
                        "--output", str(output), option,
                    ], capture_output=True, text=True)
                    self.assertEqual(result.returncode, 1, result.stderr)
                    self.assertIn("usage:", result.stderr)
                    self.assertEqual(result.stdout, "")
                    self.assertEqual(output.read_text(), "previous result")
                    self.assertEqual(sorted(p.name for p in Path(directory_name).iterdir()),
                                     ["input.bin", "output.csv"])

    def test_book_lifecycle_and_replay(self):
        data = (directory() + add(1, "B", 100, 1000000) + add(2, "B", 50, 1000000, kind="F") +
                add(3, "S", 80, 1001000) + add(4, "S", 20, 1002000) +
                reduce("E", 1, 40) + reduce("C", 3, 30) + reduce("X", 2, 20) +
                replace(1, 5, 25, 999000) + delete(3) + delete(4))
        result = self.run_feed(data)
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = list(csv.DictReader(io.StringIO(result.stdout)))
        self.assertEqual(len(rows), 10)
        self.assertEqual((rows[2]["bid"], rows[2]["bid_shares"], rows[2]["ask"], rows[2]["ask_shares"]),
                         ("100.0000", "150", "100.1000", "80"))
        self.assertEqual((rows[7]["bid"], rows[7]["bid_shares"], rows[7]["bid_levels"]),
                         ("100.0000", "30", "2"))
        self.assertEqual((rows[-1]["ask"], rows[-1]["ask_levels"]), ("", "0"))
        self.assertIn("verified=yes", result.stderr)

    def test_other_symbol_ignored(self):
        system_event = frame(common("S", 0) + b"O")
        result = self.run_feed(system_event + directory() + add(9, "B", 5, 1000000, name="OTHER", locate=8) +
                               add(1, "S", 10, 1001000))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(list(csv.DictReader(io.StringIO(result.stdout)))), 1)

    def test_optional_byte_offsets_bound_each_frame(self):
        ignored = frame(common("S", 0) + b"O")
        first = directory()
        second = add(1, "B", 5, 1000000)
        third = add(2, "S", 6, 1001000)
        feed = ignored + first + second + third

        result = self.run_feed(feed, extra_args=("--with-offset",))
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = list(csv.DictReader(io.StringIO(result.stdout)))
        self.assertEqual(len(rows), 2)
        self.assertEqual(list(rows[0]), ["frame", "timestamp_ns", "type", "bid",
                                         "bid_shares", "ask", "ask_shares", "bid_levels",
                                         "ask_levels", "bid_depth", "ask_depth",
                                         "byte_offset", "byte_end_offset"])
        self.assertEqual([row["byte_offset"] for row in rows],
                         [str(len(ignored + first)), str(len(ignored + first + second))])
        self.assertEqual([row["byte_end_offset"] for row in rows],
                         [str(len(ignored + first + second)), str(len(feed))])
        for row in rows:
            offset = int(row["byte_offset"])
            length = struct.unpack_from(">H", feed, offset)[0]
            self.assertEqual(int(row["byte_end_offset"]), offset + 2 + length)
            self.assertEqual(chr(feed[offset + 2]), row["type"])
            self.assertIn(length, (36, 40))

        default = self.run_feed(feed)
        self.assertEqual(default.returncode, 0, default.stderr)
        self.assertNotIn("byte_offset", next(csv.reader(io.StringIO(default.stdout))))

    def test_every_skipped_type_is_length_checked(self):
        for kind, length in SKIPPED_LENGTHS.items():
            with self.subTest(type=kind):
                valid = frame(kind.encode() + bytes(length - 1))
                result = self.run_feed(directory() + valid + add(1, "B", 5, 1000000))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(len(list(csv.DictReader(io.StringIO(result.stdout)))), 1)
                self.assertIn("frames=3", result.stderr)

                invalid = frame(kind.encode() + bytes(length - 2))
                result = self.run_feed(directory() + invalid)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(f"frame 2: invalid {kind} length", result.stderr)

    def test_every_book_message_type_is_length_checked(self):
        valid_frames = [
            directory(), add(1, "B", 5, 1000000), add(1, "B", 5, 1000000, kind="F"),
            reduce("E", 1, 1), reduce("C", 1, 1), reduce("X", 1, 1),
            delete(1), replace(1, 2, 5, 1000000),
        ]
        for valid in valid_frames:
            kind = chr(valid[2])
            with self.subTest(type=kind):
                shortened = frame(valid[2:-1])
                result = self.run_feed(shortened)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(f"frame 1: invalid {kind} length", result.stderr)

    def test_later_corruption_reports_first_bad_frame(self):
        prefix = directory() + add(1, "B", 10, 1000000)
        cases = [
            (prefix + b"\x00", "truncated frame length after frame 2"),
            (prefix + b"\x00\x24A", "frame 3: truncated payload"),
            (prefix + frame(b"A"), "frame 3: invalid A length"),
            (prefix + frame(b"Z"), "frame 3: unknown ITCH type"),
            (prefix, "missing BinaryFILE end marker after frame 2"),
            (prefix + b"\x00\x00X", "bytes after BinaryFILE end marker after frame 2"),
            (prefix + directory(name="TEST", locate=8), "frame 3: symbol changed stock locate"),
            (prefix + directory(name="OTHER", locate=7), "frame 3: stock locate belongs to another symbol"),
        ]
        for data, error in cases:
            with self.subTest(error=error):
                result = self.run_feed(data, complete=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(error, result.stderr)

    def test_implicit_eof_requires_explicit_option_and_final_end_of_messages(self):
        end_messages = frame(common("S", 0) + b"C")
        prefix = directory() + add(1, "B", 10, 1000000)
        strict = self.run_feed(prefix + end_messages, complete=False)
        self.assertNotEqual(strict.returncode, 0)
        self.assertIn("missing BinaryFILE end marker after frame 3", strict.stderr)

        allowed = self.run_feed(prefix + end_messages, complete=False,
                                extra_args=("--allow-implicit-eof",))
        self.assertEqual(allowed.returncode, 0, allowed.stderr)
        self.assertIn("frames=3", allowed.stderr)
        self.assertEqual(len(list(csv.DictReader(io.StringIO(allowed.stdout)))), 1)

        for bad in (prefix, prefix + end_messages + add(2, "S", 2, 1001000),
                    prefix + end_messages + b"\x00", prefix + frame(common("S", 7) + b"C")):
            with self.subTest(bad=bad[-16:]):
                result = self.run_feed(bad, complete=False,
                                       extra_args=("--allow-implicit-eof",))
                self.assertNotEqual(result.returncode, 0)

    def test_output_file_is_published_only_on_success(self):
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            source = root / "source.bin"
            destination = root / "book.csv"
            command = [str(EXE), "--input", str(source), "--symbol", "TEST",
                       "--output", str(destination), "--verify"]
            valid_prefix = directory() + add(1, "B", 10, 1000000)

            destination.write_text("previous complete result\n")
            source.write_bytes(valid_prefix + frame(b"Z") + b"\x00\x00")
            failed = subprocess.run(command, text=True, capture_output=True)
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn("frame 3: unknown ITCH type", failed.stderr)
            self.assertEqual(destination.read_text(), "previous complete result\n")
            self.assertEqual(list(root.glob(".book.csv.partial.*")), [])

            destination.unlink()
            failed_without_old_file = subprocess.run(command, text=True, capture_output=True)
            self.assertNotEqual(failed_without_old_file.returncode, 0)
            self.assertFalse(destination.exists())
            self.assertEqual(list(root.glob(".book.csv.partial.*")), [])

            source.write_bytes(valid_prefix + b"\x00\x00")
            succeeded = subprocess.run(command, text=True, capture_output=True)
            self.assertEqual(succeeded.returncode, 0, succeeded.stderr)
            rows = list(csv.DictReader(io.StringIO(destination.read_text())))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["bid_depth"], "100.0000:10")
            self.assertEqual(list(root.glob(".book.csv.partial.*")), [])

            published = destination.read_text()
            destination.write_text("older result\n")
            replacement = subprocess.run(command, text=True, capture_output=True)
            self.assertEqual(replacement.returncode, 0, replacement.stderr)
            self.assertEqual(destination.read_text(), published)

            original_input = source.read_bytes()
            same_file = subprocess.run([str(EXE), "--input", str(source), "--symbol", "TEST",
                                        "--output", str(source)], text=True, capture_output=True)
            self.assertNotEqual(same_file.returncode, 0)
            self.assertIn("input and output refer to the same file", same_file.stderr)
            self.assertEqual(source.read_bytes(), original_input)

    def checkpoint_command(self, source, *args):
        return subprocess.run([str(EXE), "--input", str(source), "--symbol", "TEST", *args],
                              text=True, capture_output=True)

    def checkpoint_layout(self, data):
        """Independent parser for v1/v2 metadata and mutation positions."""
        body = data[:-64]
        self.assertEqual(hashlib.sha256(body).hexdigest().encode(), data[-64:])
        self.assertEqual(body[:8], b"ITCHCP01")
        version = struct.unpack_from(">I", body, 8)[0]
        self.assertIn(version, (1, 2))
        pos = 12
        fields = {}
        for key in ("size", "digest", "symbol", "next_frame", "offset", "selected", "hash", "depth"):
            fields[key] = pos
            n = struct.unpack_from(">Q", body, pos)[0]
            pos += 8
            if key in ("digest", "symbol"):
                fields[key + "_value"] = body[pos:pos+n]
                pos += n
            else:
                fields[key + "_value"] = n
        for key in ("with_offset", "implicit_eof", "last_end", "has_locate"):
            fields[key] = pos
            pos += 1
        fields["locate"] = pos
        pos += 2
        fields["order_count"] = pos
        count = struct.unpack_from(">Q", body, pos)[0]
        pos += 8
        fields["orders"] = pos
        pos += count * 17
        fields["bid_count"] = pos
        count = struct.unpack_from(">Q", body, pos)[0]
        fields["bids"] = pos + 8
        pos += 8 + count * 12
        fields["ask_count"] = pos
        fields["asks"] = pos + 8
        pos += 8 + struct.unpack_from(">Q", body, pos)[0] * 12
        fields["version"] = version
        if version == 2:
            fields["csv_binding"] = pos
            count = struct.unpack_from(">Q", body, pos)[0]
            fields["csv_path_value"] = body[pos+8:pos+8+count].decode()
            pos += 8 + count
            fields["csv_bytes"] = pos
            fields["csv_bytes_value"] = struct.unpack_from(">Q", body, pos)[0]
        return fields

    def test_checkpoint_suffix_and_final_state_at_every_boundary(self):
        payloads = [frame(common("S", 0) + b"O"), directory(),
                    add(90, "B", 3, 900000, name="OTHER", locate=8),
                    add(1, "B", 100, 1000000), add(2, "B", 50, 1000000, kind="F"),
                    add(3, "S", 80, 1001000), add(4, "S", 20, 1002000),
                    reduce("E", 1, 40), reduce("C", 3, 30), reduce("X", 2, 50),
                    replace(1, 5, 25, 999000), delete(3), delete(4), delete(5),
                    add(6, "B", 11, 990000), add(7, "S", 12, 1010000),
                    add(8, "B", 13, 990000), frame(common("S", 0) + b"C")]
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            source, checkpoint, full_end, resumed_end = [root / n for n in
                ("feed.bin", "saved.cp", "full.cp", "resumed.cp")]
            for flags, marker in (((), b"\0\0"),
                    (("--depth", "1", "--with-offset", "--allow-implicit-eof"), b"")):
                source.write_bytes(b"".join(payloads) + marker)
                full = self.checkpoint_command(source, *flags, "--verify", "--check-book",
                                               "--checkpoint-out", str(full_end))
                self.assertEqual(full.returncode, 0, full.stderr)
                lines = full.stdout.splitlines()
                for boundary in range(len(payloads) + 1):
                    with self.subTest(flags=flags, boundary=boundary):
                        prefix = self.checkpoint_command(source, *flags, "--verify", "--check-book",
                            "--stop-after-frame", str(boundary), "--checkpoint-out", str(checkpoint))
                        self.assertEqual(prefix.returncode, 0, prefix.stderr)
                        self.assertIn("scope=prefix complete=no", prefix.stderr)
                        layout = self.checkpoint_layout(checkpoint.read_bytes())
                        self.assertEqual(layout["offset_value"], sum(map(len, payloads[:boundary])))
                        self.assertEqual(layout["next_frame_value"], boundary + 1)
                        self.assertEqual(layout["digest_value"], hashlib.sha256(source.read_bytes()).hexdigest().encode())
                        resumed = self.checkpoint_command(source, *flags, "--verify", "--check-book",
                            "--resume", str(checkpoint), "--checkpoint-out", str(resumed_end))
                        self.assertEqual(resumed.returncode, 0, resumed.stderr)
                        self.assertIn("scope=suffix complete=yes", resumed.stderr)
                        expected = [lines[0]] + [line for line in lines[1:] if int(line.split(",")[0]) > boundary]
                        self.assertEqual(resumed.stdout.splitlines(), expected)
                        self.assertEqual(prefix.stdout.splitlines()[1:] + resumed.stdout.splitlines()[1:], lines[1:])
                        # Equal bytes cover every final order, level, locate, counter and cumulative CSV hash.
                        self.assertEqual(resumed_end.read_bytes(), full_end.read_bytes())
                terminal = self.checkpoint_command(source, *flags, "--resume", str(full_end))
                self.assertEqual(terminal.returncode, 0, terminal.stderr)
                self.assertEqual(terminal.stdout.splitlines(), [lines[0]])

    def test_checkpoint_chained_resume_and_empty_session(self):
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            source, saved = root / "feed.bin", root / "saved.cp"
            source.write_bytes(directory() + add(1, "B", 10, 1000000) +
                               replace(1, 2, 5, 999000) + delete(2) + b"\0\0")
            full = self.checkpoint_command(source)
            first = self.checkpoint_command(source, "--stop-after-frame", "2", "--checkpoint-out", str(saved))
            self.assertEqual(first.returncode, 0, first.stderr)
            middle = self.checkpoint_command(source, "--resume", str(saved), "--stop-after-frame", "3",
                                             "--checkpoint-out", str(saved), "--verify")
            self.assertEqual(middle.returncode, 0, middle.stderr)
            last = self.checkpoint_command(source, "--resume", str(saved))
            self.assertEqual(last.returncode, 0, last.stderr)
            self.assertEqual(first.stdout.splitlines() + middle.stdout.splitlines()[1:] +
                             last.stdout.splitlines()[1:], full.stdout.splitlines())
            source.write_bytes(b"\0\0")
            empty = self.checkpoint_command(source, "--checkpoint-out", str(saved), "--verify")
            self.assertEqual(empty.returncode, 0, empty.stderr)
            restored = self.checkpoint_command(source, "--resume", str(saved), "--verify")
            self.assertEqual(restored.returncode, 0, restored.stderr)
            self.assertEqual(empty.stdout, restored.stdout)

    def test_periodic_checkpoint_saves_skipped_frames_before_error(self):
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            source, saved, output = root / "feed.bin", root / "saved.cp", root / "out.csv"
            good = directory() + frame(common("S", 0) + b"O")
            source.write_bytes(good + frame(b"Z") + b"\0\0")
            output.write_text("previous completed CSV")
            for with_csv in (False, True):
                args = ("--output", str(output)) if with_csv else ()
                result = self.checkpoint_command(source, "--checkpoint-every-frames", "2",
                    "--checkpoint-out", str(saved), *args)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("frame 3: unknown", result.stderr)
                layout = self.checkpoint_layout(saved.read_bytes())
                self.assertEqual(layout["next_frame_value"], 3)
                self.assertEqual(layout["offset_value"], len(good))
                self.assertEqual(layout["selected_value"], 0)
                self.assertEqual(layout["version"], 2 if with_csv else 1)
                if with_csv:
                    journal = Path(layout["csv_path_value"])
                    self.assertTrue(journal.exists())
                    self.assertEqual(journal.stat().st_size, layout["csv_bytes_value"])
                self.assertEqual(output.read_text(), "previous completed CSV")

    def test_resume_csv_matches_full_replay_and_supports_multiple_stops(self):
        payloads = [directory(), add(1, "B", 10, 1000000),
                    frame(common("W", 0) + b"0"), add(2, "S", 20, 1001000),
                    replace(1, 3, 5, 999000), reduce("C", 2, 10), delete(3)]
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            source, saved, output = root / "feed.bin", root / "saved.cp", root / "out.csv"
            source.write_bytes(b"".join(payloads) + b"\0\0")
            flags = ("--with-offset", "--depth", "1", "--verify", "--check-book")
            full = self.checkpoint_command(source, *flags)
            self.assertEqual(full.returncode, 0, full.stderr)
            for boundary in range(len(payloads) + 1):
                first = self.checkpoint_command(source, *flags, "--stop-after-frame", str(boundary),
                    "--checkpoint-every-frames", "2", "--checkpoint-out", str(saved), "--output", str(output))
                self.assertEqual(first.returncode, 0, first.stderr)
                layout = self.checkpoint_layout(saved.read_bytes())
                self.assertEqual(layout["version"], 2)
                # Uncheckpointed bytes, even complete duplicate rows, must be discarded.
                with Path(layout["csv_path_value"]).open("ab") as journal:
                    journal.write(b"uncheckpointed partial row,123")
                second = self.checkpoint_command(source, *flags, "--resume", str(saved), "--resume-csv",
                    "--output", str(output), "--checkpoint-every-frames", "2")
                self.assertEqual(second.returncode, 0, second.stderr)
                self.assertIn("scope=full complete=yes", second.stderr)
                self.assertEqual(output.read_text(), full.stdout)
                again = self.checkpoint_command(source, *flags, "--resume", str(saved), "--resume-csv",
                                                "--output", str(output))
                self.assertEqual(again.returncode, 0, again.stderr)
                self.assertEqual(output.read_text(), full.stdout)
            # Chained stops retain the full prefix, not just the newest suffix.
            first = self.checkpoint_command(source, *flags, "--stop-after-frame", "2",
                                           "--checkpoint-out", str(saved), "--output", str(output))
            self.assertEqual(first.returncode, 0, first.stderr)
            for boundary in (4, 6):
                part = self.checkpoint_command(source, *flags, "--resume", str(saved), "--resume-csv",
                    "--output", str(output), "--stop-after-frame", str(boundary))
                self.assertEqual(part.returncode, 0, part.stderr)
                self.assertIn("scope=prefix complete=no", part.stderr)
            end = self.checkpoint_command(source, *flags, "--resume", str(saved), "--resume-csv",
                                         "--output", str(output))
            self.assertEqual(end.returncode, 0, end.stderr)
            self.assertEqual(output.read_text(), full.stdout)

    def test_resume_csv_rejects_missing_or_corrupt_prefix_without_overwriting(self):
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            source, saved, output = root / "feed.bin", root / "saved.cp", root / "out.csv"
            source.write_bytes(directory() + add(1, "B", 10, 1000000) + delete(1) + b"\0\0")
            first = self.checkpoint_command(source, "--stop-after-frame", "2", "--checkpoint-out", str(saved),
                                           "--output", str(output))
            self.assertEqual(first.returncode, 0, first.stderr)
            cp = saved.read_bytes()
            layout = self.checkpoint_layout(cp)
            journal = Path(layout["csv_path_value"])
            original = journal.read_bytes()
            for damaged, error in ((original[:-1], "truncated CSV prefix"),
                    (b"x" + original[1:], "header mismatch"),
                    (original.replace(b"100.0000:10", b"100.0000:11"), "hash mismatch")):
                journal.write_bytes(damaged)
                output.write_text("previous full CSV")
                result = self.checkpoint_command(source, "--resume", str(saved), "--resume-csv", "--output", str(output))
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(error, result.stderr)
                self.assertEqual(saved.read_bytes(), cp)
                self.assertEqual(output.read_text(), "previous full CSV")
            journal.unlink()
            result = self.checkpoint_command(source, "--resume", str(saved), "--resume-csv", "--output", str(output))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("cannot open saved CSV prefix", result.stderr)
            self.assertEqual(saved.read_bytes(), cp)
            state_only = self.checkpoint_command(source, "--checkpoint-out", str(saved))
            self.assertEqual(state_only.returncode, 0, state_only.stderr)
            result = self.checkpoint_command(source, "--resume", str(saved), "--resume-csv", "--output", str(output))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("no saved CSV prefix", result.stderr)

    def test_periodic_checkpoint_recovers_after_process_is_killed(self):
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            source, saved, output, expected = [root / name for name in ("feed.bin", "saved.cp", "out.csv", "full.csv")]
            source.write_bytes(directory() + (add(1, "B", 10, 1000000) + delete(1)) * 200000 + b"\0\0")
            output.write_text("previous completed CSV")
            command = [str(EXE), "--input", str(source), "--symbol", "TEST", "--output", str(output),
                       "--checkpoint-out", str(saved), "--checkpoint-every-frames", "1000"]
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                deadline = time.monotonic() + 15
                while not saved.exists() and process.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.005)
                self.assertTrue(saved.exists(), "no periodic checkpoint published")
                self.assertIsNone(process.poll(), "replay completed before interruption")
                process.kill()
                process.communicate(timeout=5)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate(timeout=5)
            layout = self.checkpoint_layout(saved.read_bytes())
            self.assertLess(layout["next_frame_value"], 400002)
            self.assertEqual(output.read_text(), "previous completed CSV")
            resumed = self.checkpoint_command(source, "--resume", str(saved), "--resume-csv",
                                              "--output", str(output), "--verify")
            self.assertEqual(resumed.returncode, 0, resumed.stderr)
            full = self.checkpoint_command(source, "--output", str(expected))
            self.assertEqual(full.returncode, 0, full.stderr)
            self.assertEqual(hashlib.sha256(output.read_bytes()).digest(), hashlib.sha256(expected.read_bytes()).digest())
            self.assertEqual(output.stat().st_size, expected.stat().st_size)

    def test_checkpoint_save_rules_do_not_depend_on_csv_mode(self):
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            source, saved, other, output = [root / name for name in
                ("feed.bin", "saved.cp", "other.cp", "out.csv")]
            source.write_bytes(directory() + add(1, "B", 10, 1000000) +
                               add(2, "S", 20, 1001000) + delete(1) +
                               frame(common("S", 0) + b"O") + delete(2) + b"\0\0")
            full = self.checkpoint_command(source)
            created = self.checkpoint_command(source, "--stop-after-frame", "3", "--checkpoint-out", str(saved),
                                              "--output", str(output))
            self.assertEqual(created.returncode, 0, created.stderr)
            original = saved.read_bytes()
            suffix = self.checkpoint_command(source, "--resume", str(saved))
            self.assertEqual(suffix.returncode, 0, suffix.stderr)
            self.assertEqual(saved.read_bytes(), original)
            csv_only = self.checkpoint_command(source, "--resume", str(saved), "--resume-csv",
                                                "--output", str(output), "--verify")
            self.assertEqual(csv_only.returncode, 0, csv_only.stderr)
            self.assertEqual(output.read_text(), full.stdout)
            self.assertEqual(saved.read_bytes(), original, "CSV continuation must not enable saving")
            # Both CSV modes use the same inferred destination when saving is requested.
            modes = ((False, ()), (False, ("--output", str(output))),
                     (True, ("--resume-csv", "--output", str(output))))
            for csv_mode, extra in modes:
                saved.write_bytes(original)
                automatic = self.checkpoint_command(source, "--resume", str(saved),
                    "--checkpoint-every-frames", "2", "--verify", *extra)
                self.assertEqual(automatic.returncode, 0, automatic.stderr)
                if extra:
                    expected = full.stdout.splitlines() if csv_mode else [full.stdout.splitlines()[0]] + [
                        row for row in full.stdout.splitlines()[1:] if int(row.split(",")[0]) > 3]
                    self.assertEqual(output.read_text().splitlines(), expected)
                self.assertEqual(self.checkpoint_layout(saved.read_bytes())["next_frame_value"], 7)
                saved.write_bytes(original)
                explicit = self.checkpoint_command(source, "--resume", str(saved), "--checkpoint-out", str(other),
                    "--checkpoint-every-frames", "2", *extra)
                self.assertEqual(explicit.returncode, 0, explicit.stderr)
                self.assertEqual(saved.read_bytes(), original, "explicit destination must take precedence")
                self.assertEqual(self.checkpoint_layout(other.read_bytes())["next_frame_value"], 7)
                saved.write_bytes(original)
                stopped = self.checkpoint_command(source, "--resume", str(saved), "--stop-after-frame", "4", *extra)
                self.assertEqual(stopped.returncode, 0, stopped.stderr)
                self.assertEqual(self.checkpoint_layout(saved.read_bytes())["next_frame_value"], 5)

    def test_inferred_periodic_destination_is_written_before_later_failure(self):
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            source, saved = root / "feed.bin", root / "saved.cp"
            source.write_bytes(directory() + add(1, "B", 10, 1000000) +
                               frame(common("S", 0) + b"O") + delete(1) + frame(b"Z") + b"\0\0")
            first = self.checkpoint_command(source, "--checkpoint-out", str(saved), "--stop-after-frame", "3")
            self.assertEqual(first.returncode, 0, first.stderr)
            resumed = self.checkpoint_command(source, "--resume", str(saved), "--checkpoint-every-frames", "2")
            self.assertNotEqual(resumed.returncode, 0)
            self.assertIn("frame 5: unknown", resumed.stderr)
            state = self.checkpoint_layout(saved.read_bytes())
            self.assertEqual(state["next_frame_value"], 5)
            self.assertEqual(struct.unpack_from(">Q", saved.read_bytes(), state["order_count"])[0], 0)

    def test_periodic_checkpoint_options_are_explicit_and_validated(self):
        for args, error in ((("--checkpoint-every-frames", "0"), "must be positive"),
                (("--checkpoint-every-frames", "-1"), "must be"),
                (("--checkpoint-every-frames", "abc"), "must be"),
                (("--checkpoint-every-frames", "18446744073709551615"), "must be"),
                (("--checkpoint-every-frames", "10"), "requires --checkpoint-out"),
                (("--resume-csv",), "requires --resume and --output")):
            result = self.run_feed(directory(), extra_args=args)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(error, result.stderr)
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            source, saved, output = root / "feed.bin", root / "saved.cp", root / "out.csv"
            source.write_bytes(directory() + b"\0\0")
            first = self.checkpoint_command(source, "--checkpoint-out", str(saved), "--output", str(output))
            self.assertEqual(first.returncode, 0, first.stderr)
            layout = self.checkpoint_layout(saved.read_bytes())
            journal = Path(layout["csv_path_value"])
            original = journal.read_bytes()
            for args in (("--output", str(journal), "--resume-csv"),
                         ("--checkpoint-out", str(journal))):
                result = self.checkpoint_command(source, "--resume", str(saved), *args)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("must be distinct", result.stderr)
                self.assertEqual(journal.read_bytes(), original)
            result = self.checkpoint_command(source, "--resume", str(saved), "--output", str(output),
                "--checkpoint-out", str(saved), "--checkpoint-every-frames", "1")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.checkpoint_layout(saved.read_bytes())["version"], 1)
            self.assertEqual(len(output.read_text().splitlines()), 1)

    def test_sha256_padding_and_streaming_against_hashlib(self):
        with tempfile.TemporaryDirectory() as directory_name:
            source, saved = Path(directory_name) / "feed.bin", Path(directory_name) / "saved.cp"
            for size in (0, 1, 55, 56, 63, 64, 65, 127, 128, 1000000):
                with self.subTest(size=size):
                    source.write_bytes(bytes(i % 251 for i in range(size)))
                    result = self.checkpoint_command(source, "--stop-after-frame", "0", "--checkpoint-out", str(saved))
                    self.assertEqual(result.returncode, 0, result.stderr)
                    layout = self.checkpoint_layout(saved.read_bytes())
                    self.assertEqual(layout["size_value"], size)
                    self.assertEqual(layout["digest_value"], hashlib.sha256(source.read_bytes()).hexdigest().encode())

    def test_checkpoint_corruption_and_semantic_validation_preserve_outputs(self):
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            source, saved, output, new_cp = [root / n for n in ("feed.bin", "saved.cp", "book.csv", "new.cp")]
            source.write_bytes(directory() + add(1, "B", 10, 1000000) +
                               add(2, "B", 20, 1000000) + delete(1) + b"\0\0")
            created = self.checkpoint_command(source, "--stop-after-frame", "3", "--checkpoint-out", str(saved))
            self.assertEqual(created.returncode, 0, created.stderr)
            original = saved.read_bytes()
            f = self.checkpoint_layout(original)

            def changed(offset, value):
                body = bytearray(original[:-64])
                body[offset:offset+len(value)] = value
                return bytes(body) + hashlib.sha256(body).hexdigest().encode()

            cases = [
                (original[:-1], "checksum"), (b"broken", "truncated"),
                (original[:30] + bytes([original[30] ^ 1]) + original[31:], "checksum"),
                (changed(0, b"INVALID!"), "magic"),
                (changed(8, struct.pack(">I", 3)), "version"),
                (changed(f["offset"], struct.pack(">Q", f["offset_value"]-1)), "frame boundary"),
                (changed(f["next_frame"], struct.pack(">Q", 3)), "frame/offset"),
                (changed(f["last_end"], b"\1"), "end-message"),
                (changed(f["with_offset"], b"\2"), "invalid flag"),
                (changed(f["orders"] + 8, b"Q"), "invalid live order"),
                (changed(f["orders"] + 9, bytes(4)), "invalid live order"),
                (changed(f["orders"] + 17, original[f["orders"]:f["orders"]+8]), "duplicate order"),
                (changed(f["bids"] + 4, struct.pack(">Q", 999)), "price levels"),
                (changed(f["order_count"], struct.pack(">Q", 2**64-1)), "order count"),
                (changed(f["depth"], struct.pack(">Q", 1001)), "invalid depth"),
            ]
            for damaged, reason in cases:
                with self.subTest(reason=reason):
                    saved.write_bytes(damaged)
                    output.write_text("previous CSV")
                    new_cp.write_bytes(b"previous checkpoint")
                    result = self.checkpoint_command(source, "--resume", str(saved),
                        "--output", str(output), "--checkpoint-out", str(new_cp), "--verify")
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(reason, result.stderr)
                    self.assertEqual(output.read_text(), "previous CSV")
                    self.assertEqual(new_cp.read_bytes(), b"previous checkpoint")
                    self.assertEqual(list(root.glob(".*.partial.*")), [])

            saved.write_bytes(original)
            for args, reason in ((("--symbol", "OTHER"), "symbol mismatch"),
                    (("--depth", "2"), "options mismatch"),
                    (("--with-offset",), "options mismatch"),
                    (("--allow-implicit-eof",), "options mismatch")):
                result = self.checkpoint_command(source, "--resume", str(saved), "--output", str(output), *args)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(reason, result.stderr)
                self.assertEqual(output.read_text(), "previous CSV")
            # Same-size change beyond the checkpoint must still be detected.
            source.write_bytes(source.read_bytes()[:-1] + b"X")
            result = self.checkpoint_command(source, "--resume", str(saved), "--output", str(output))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("input SHA-256 mismatch", result.stderr)
            self.assertEqual(output.read_text(), "previous CSV")

    def test_checkpoint_bad_suffix_points_to_untrusted_byte(self):
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            source, saved, output = root / "feed.bin", root / "saved.cp", root / "out.csv"
            prefix = directory() + add(1, "B", 10, 1000000)
            source.write_bytes(prefix + frame(b"Z") + b"\0\0")
            created = self.checkpoint_command(source, "--stop-after-frame", "2", "--checkpoint-out", str(saved))
            self.assertEqual(created.returncode, 0, created.stderr)
            original = saved.read_bytes()
            output.write_text("previous")
            result = self.checkpoint_command(source, "--resume", str(saved), "--output", str(output),
                                             "--checkpoint-out", str(saved))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("frame 3: unknown ITCH type", result.stderr)
            self.assertIn(f"first untrusted byte_offset={len(prefix)}", result.stderr)
            self.assertEqual(saved.read_bytes(), original)
            self.assertEqual(output.read_text(), "previous")

    def test_checkpoint_argument_and_path_validation(self):
        for value in ("-1", "1x", "18446744073709551615", "18446744073709551616"):
            result = self.run_feed(directory(), extra_args=("--stop-after-frame", value))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("--stop-after-frame must be", result.stderr)
        result = self.run_feed(directory(), extra_args=("--stop-after-frame", "0"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("requires --checkpoint-out", result.stderr)
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            source, saved, output = root / "feed.bin", root / "saved.cp", root / "out.csv"
            source.write_bytes(directory() + b"\0\0")
            saved.write_bytes(b"previous")
            result = self.checkpoint_command(source, "--stop-after-frame", "2", "--checkpoint-out", str(saved))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("exceeds input", result.stderr)
            self.assertEqual(saved.read_bytes(), b"previous")
            for args in (("--checkpoint-out", str(source)),
                         ("--checkpoint-out", str(output), "--output", str(output)),
                         ("--resume", str(saved), "--output", str(saved))):
                result = self.checkpoint_command(source, *args)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("must be distinct", result.stderr)
            alias = root / "alias.cp"
            alias.symlink_to(source)
            result = self.checkpoint_command(source, "--checkpoint-out", str(alias))
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(source.read_bytes(), directory() + b"\0\0")

    def test_every_event_matches_independent_reference(self):
        rng = random.Random(20260926)
        payloads = [directory()]
        orders = {}
        expected = {2: [], 5: []}
        next_id = 1
        prices = [990000, 995000, 1000000, 1001000, 1002000, 1010000, 1020000]

        def record(payload, kind, timestamp):
            payloads.append(payload)
            for limit in expected:
                expected[limit].append(reference_row(orders, len(payloads), timestamp, kind, limit))

        # Multiple orders share prices; more than five levels exist on both sides.
        for side in ("B", "S"):
            for raw_price in prices:
                for _ in range(2):
                    order_id = next_id
                    next_id += 1
                    shares = rng.randint(1, 500)
                    orders[order_id] = (side, shares, raw_price)
                    kind = "F" if order_id % 3 == 0 else "A"
                    record(add(order_id, side, shares, raw_price, kind=kind,
                               timestamp=next_id), kind, next_id)

        for step in range(250):
            timestamp = 1000 + step
            action = rng.choice(("add", "reduce", "delete", "replace"))
            if not orders:
                action = "add"
            if action == "add":
                order_id = next_id
                next_id += 1
                side = rng.choice(("B", "S"))
                shares = rng.randint(1, 500)
                raw_price = rng.choice(prices)
                orders[order_id] = (side, shares, raw_price)
                kind = rng.choice(("A", "F"))
                record(add(order_id, side, shares, raw_price, kind=kind, timestamp=timestamp),
                       kind, timestamp)
            else:
                old_id = rng.choice(tuple(orders))
                side, old_shares, raw_price = orders[old_id]
                if action == "reduce":
                    removed = rng.randint(1, old_shares)
                    kind = rng.choice(("E", "C", "X"))
                    if removed == old_shares:
                        del orders[old_id]
                    else:
                        orders[old_id] = (side, old_shares - removed, raw_price)
                    record(reduce(kind, old_id, removed, timestamp=timestamp), kind, timestamp)
                elif action == "delete":
                    del orders[old_id]
                    record(delete(old_id, timestamp=timestamp), "D", timestamp)
                else:
                    new_id = next_id
                    next_id += 1
                    new_shares = rng.randint(1, 500)
                    new_price = rng.choice(prices)
                    del orders[old_id]
                    orders[new_id] = (side, new_shares, new_price)
                    record(replace(old_id, new_id, new_shares, new_price,
                                   timestamp=timestamp), "U", timestamp)

        data = b"".join(payloads)
        for limit, rows_expected in expected.items():
            with self.subTest(depth=limit):
                result = self.run_feed(data, extra_args=("--depth", str(limit), "--check-book"))
                self.assertEqual(result.returncode, 0, result.stderr)
                rows_actual = list(csv.DictReader(io.StringIO(result.stdout)))
                self.assertEqual(rows_actual, rows_expected)
                self.assertIn("verified=yes", result.stderr)
                # Restored books must also agree with the independent order model.
                with tempfile.TemporaryDirectory() as directory_name:
                    source = Path(directory_name) / "random.bin"
                    checkpoint = Path(directory_name) / "random.cp"
                    source.write_bytes(data + b"\0\0")
                    for boundary in (5, 63, 190, len(payloads)):
                        saved = self.checkpoint_command(source, "--depth", str(limit), "--check-book",
                            "--stop-after-frame", str(boundary), "--checkpoint-out", str(checkpoint))
                        self.assertEqual(saved.returncode, 0, saved.stderr)
                        restored = self.checkpoint_command(source, "--depth", str(limit), "--check-book",
                            "--resume", str(checkpoint), "--verify")
                        self.assertEqual(restored.returncode, 0, restored.stderr)
                        self.assertEqual(list(csv.DictReader(io.StringIO(restored.stdout))),
                            [row for row in rows_expected if int(row["frame"]) > boundary])

    def test_empty_and_single_level_depth(self):
        data = directory() + add(1, "B", 5, 1000000) + delete(1)
        result = self.run_feed(data, extra_args=("--depth", "5", "--check-book"))
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = list(csv.DictReader(io.StringIO(result.stdout)))
        self.assertEqual((rows[0]["bid_depth"], rows[0]["ask_depth"]), ("100.0000:5", ""))
        self.assertEqual((rows[1]["bid_depth"], rows[1]["ask_depth"]), ("", ""))

    def test_depth_argument_validation(self):
        for value in ("0", "-1", "1001", "abc", "2x"):
            with self.subTest(value=value):
                result = self.run_feed(directory(), extra_args=("--depth", value))
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("--depth must be", result.stderr)

    def test_malformed_and_simulated_missing_or_out_of_order(self):
        scenarios = [
            (b"\x00", "truncated frame length"),
            (b"\x00\x24A", "truncated payload"),
            (frame(b"A"), "invalid A length"),
            (frame(b"Z"), "unknown ITCH type"),
            (directory() + reduce("X", 404, 2), "unknown order reference"),  # missing add
            (directory() + reduce("E", 1, 2) + add(1, "B", 10, 1000000), "unknown order reference"),  # reordered
            (directory() + add(1, "B", 10, 1000000) + reduce("X", 1, 11), "invalid share reduction"),
            (directory() + add(1, "B", 10, 1000000) + add(1, "B", 10, 1000000), "duplicate order"),
            (directory() + add(1, "B", 0, 1000000), "invalid add fields"),
            (directory() + add(1, "Q", 10, 1000000), "invalid add fields"),
            (directory() + add(1, "B", 10, 0), "invalid add fields"),
            (directory() + add(1, "B", 10, 1000000, name="OTHER"), "add symbol/locate mismatch"),
            (directory() + add(1, "B", 10, 1000000, locate=8), "add symbol/locate mismatch"),
            (directory() + add(1, "B", 10, 1000000) + replace(1, 1, 5, 999000), "invalid replacement"),
            (directory() + add(1, "B", 10, 1000000) + replace(1, 2, 0, 999000), "invalid replacement"),
        ]
        for data, reason in scenarios:
            with self.subTest(reason=reason):
                result = self.run_feed(data, complete=(reason != "truncated frame length"))
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(reason, result.stderr)
        incomplete = self.run_feed(directory(), complete=False)
        self.assertIn("missing BinaryFILE end marker", incomplete.stderr)
        trailing = self.run_feed(directory() + b"\x00\x00X", complete=False)
        self.assertIn("bytes after BinaryFILE end marker", trailing.stderr)


if __name__ == "__main__":
    EXE = Path(sys.argv[1]).resolve()
    sys.argv = [sys.argv[0]]
    unittest.main()
