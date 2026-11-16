"""Export the offline-installable Agent SDK wheel from the repository's client inventory."""

import argparse
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sim_platform.persistence.agents.sdk import SDKFiles
from sim_platform.sdk.distribution import FILENAME


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--output', type=Path, required=True, help='Output directory for the SDK wheel'
    )
    options = parser.parse_args()
    with TemporaryDirectory() as folder:
        target = SDKFiles(folder).export(options.output.resolve() / FILENAME)
        print(target)


if __name__ == '__main__':
    main()
