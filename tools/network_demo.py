#!/usr/bin/env python3
"""Run a finite local recovery demo and compare its CSV with offline replay."""
import argparse
import hashlib
import json
import socket
import subprocess
import sys
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", default="build/itch_replay")
    parser.add_argument("--output-dir", default="build/network-demo")
    args = parser.parse_args()
    exe = str(Path(args.executable).resolve())
    root = Path(args.output_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    sockets = []
    try:
        for _ in range(3):
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sockets.append(sock)
            sock.bind(("127.0.0.1", 0))
        addresses = [f"127.0.0.1:{sock.getsockname()[1]}" for sock in sockets]
    finally:
        for sock in sockets:
            sock.close()
    listen, source, recovery = addresses
    receiver = None
    script = Path(__file__).resolve().with_name("network_sender.py")
    try:
        with (root / "receiver.log").open("w") as log:
            receiver = subprocess.Popen(
                [
                    exe,
                    "--live",
                    "--listen",
                    listen,
                    "--source",
                    source,
                    "--recovery",
                    recovery,
                    "--session",
                    "SIM0000001",
                    "--symbol",
                    "TEST",
                    "--check-book",
                    "--output",
                    str(root / "live.csv"),
                ],
                stderr=log,
                stdout=subprocess.DEVNULL,
            )
            deadline = time.monotonic() + 3
            while "listening=" not in (root / "receiver.log").read_text():
                if receiver.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError("receiver failed to start; see receiver.log")
                time.sleep(0.005)
            sender = subprocess.run(
                [
                    sys.executable,
                    str(script),
                    "--destination",
                    listen,
                    "--source",
                    source,
                    "--recovery",
                    recovery,
                    "--scenario",
                    "recover",
                    "--serve-seconds",
                    "0.6",
                    "--write-fixture",
                    str(root / "synthetic.bin"),
                ],
                capture_output=True,
                text=True,
                timeout=4,
            )
            (root / "sender.log").write_text(sender.stdout + sender.stderr)
            if sender.returncode:
                raise RuntimeError("sender failed; see sender.log")
            if receiver.wait(timeout=4):
                raise RuntimeError("receiver failed; see receiver.log")
        baseline = subprocess.run(
            [
                exe,
                "--input",
                str(root / "synthetic.bin"),
                "--symbol",
                "TEST",
                "--check-book",
                "--verify",
                "--output",
                str(root / "offline.csv"),
            ],
            capture_output=True,
            text=True,
            timeout=4,
        )
        (root / "offline.log").write_text(baseline.stderr)
        if baseline.returncode:
            raise RuntimeError("offline replay failed; see offline.log")
        live, offline = (root / "live.csv").read_bytes(), (root / "offline.csv").read_bytes()
        status = (root / "receiver.log").read_text()
        passed = live == offline and "state=RECOVERING" in status and "state=COMPLETE" in status
        report = {
            "passed": passed,
            "transport": "MoldUDP64 unicast over loopback UDP",
            "scenario": "missing sequence 2, reversed tail, duplicate, retransmission",
            "csv_bytes_equal": live == offline,
            "csv_rows": len(live.splitlines()) - 1,
            "csv_sha256": hashlib.sha256(live).hexdigest(),
            "nasdaq_connection": "unverified",
            "validation": "simulated-only",
        }
        (root / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, indent=2))
        if not passed:
            raise RuntimeError("demo comparison failed")
    finally:
        if receiver and receiver.poll() is None:
            receiver.kill()
            receiver.wait()


if __name__ == "__main__":
    main()
