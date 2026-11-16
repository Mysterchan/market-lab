"""Frozen Desktop entry; multiprocessing children must bypass GUI initialization."""

import multiprocessing


if __name__ == '__main__':
    multiprocessing.freeze_support()
    import os
    import certifi

    os.environ.setdefault('SSL_CERT_FILE', certifi.where())
    import argparse
    from pathlib import Path

    parser = argparse.ArgumentParser(description='Market Lab Desktop')
    parser.add_argument('--demo', action='store_true')
    parser.add_argument('--data-dir', type=Path)
    parser.add_argument('--smoke-test-report', type=Path, help=argparse.SUPPRESS)
    args, _ = parser.parse_known_args()
    if args.smoke_test_report:
        from sim_platform.desktop.smoke import run_smoke

        raise SystemExit(run_smoke(args.smoke_test_report))
    from sim_platform.desktop.app import launch

    raise SystemExit(launch(directory=args.data_dir, demo=args.demo))
