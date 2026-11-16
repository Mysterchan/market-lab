"""Generate a repeatable synthetic workload; prints the engine's own measurements."""
import subprocess
import sys
import tempfile
from pathlib import Path

from itch_fixtures import add, delete, directory


def main():
    executable = Path(sys.argv[1]).resolve()
    pairs = int(sys.argv[2]) if len(sys.argv) > 2 else 50000
    with tempfile.TemporaryDirectory() as tmp:
        feed = Path(tmp) / "synthetic.bin"
        with feed.open("wb") as out:
            out.write(directory())
            for order in range(1, pairs + 1):
                out.write(add(order, "B", 100, 1000000, timestamp=order * 2))
                out.write(delete(order, timestamp=order * 2 + 1))
            out.write(b"\x00\x00")
        run = subprocess.run([str(executable), "--input", str(feed), "--symbol", "TEST",
                              "--verify"], text=True, stdout=subprocess.DEVNULL,
                             stderr=subprocess.PIPE)
        print(f"synthetic_bytes={feed.stat().st_size} add_delete_pairs={pairs}")
        print(run.stderr.strip())
        if run.returncode:
            raise SystemExit(run.returncode)


if __name__ == "__main__":
    main()
