"""Verify a saved runtime-v1 run in separate storage; never invoke its model."""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sim_platform import Platform


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--run', required=True)
    parser.add_argument(
        '--output', type=Path, required=True, help='empty isolated replay directory'
    )
    parser.add_argument('--library', type=Path)
    args = parser.parse_args()
    result = Platform.replay_run(args.data_dir, args.run, args.output, library=args.library)
    print(
        json.dumps(
            {
                key: result[key]
                for key in ('verified', 'run_id', 'decisions', 'fills', 'destination')
            },
            indent=2,
        )
    )


if __name__ == '__main__':
    main()
