"""Measure queued Pause/Stop acknowledgements in the current HTML Desktop."""

import argparse
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.benchmark_platform import percentile, save, sha256
from tools.desktop.driver import DesktopDriver


def main(args):
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    meta = json.loads((args.fixture_dir / 'fixture.json').read_text())
    source = args.fixture_dir / 'prefix.bin'
    if sha256(source) != meta['sha256']:
        raise ValueError('fixture digest mismatch')
    samples = []
    for policy in ('human', 'rule'):
        for repeat in range(args.repeats):
            with TemporaryDirectory(prefix='desktop-controls-') as temporary:
                driver = DesktopDriver(
                    Path(temporary) / 'accounts', (source, None), timeout=args.timeout
                )
                try:
                    driver.prepare(source, policy=policy, start_ns=meta['origin']['time_ns'])
                    driver.operation('play', speed=None)
                    target = meta['origin']['sequence'] + max(
                        1, int(meta['playback_frames'] * args.at_fraction)
                    )
                    driver.until(lambda: driver.view['market']['sequence'] >= target)
                    if driver.view['execution']['status'] != 'running':
                        raise ValueError('fixture ended before Pause probe; use a longer fixture')
                    started = time.perf_counter()
                    driver.operation('pause')
                    driver.until(lambda: driver.view['market']['status'] == 'paused')
                    paused = time.perf_counter() - started
                    sequence = driver.view['market']['sequence']
                    end = time.monotonic() + 0.1
                    driver.until(lambda: time.monotonic() >= end)
                    if driver.view['market']['sequence'] != sequence:
                        raise ValueError('source advanced after Pause acknowledgement')
                    driver.operation('play', speed=None)
                    driver.until(lambda: driver.view['market']['sequence'] > sequence)
                    started = time.perf_counter()
                    driver.operation('stop')
                    driver.until(lambda: driver.view['execution']['status'] == 'stopped')
                    stopped = time.perf_counter() - started
                    if (
                        driver.view['execution']['reserved_cash']
                        or driver.view['execution']['reserved_shares']
                    ):
                        raise ValueError('Stop did not release reservations')
                    samples.append(
                        dict(
                            policy=policy,
                            repeat=repeat + 1,
                            pause_seconds=paused,
                            stop_seconds=stopped,
                            paused_sequence=sequence,
                            stopped_sequence=driver.view['market']['sequence'],
                            pause_does_not_advance=True,
                            stop_releases_reservations=True,
                        )
                    )
                finally:
                    started = time.perf_counter()
                    driver.close()
                    if (
                        samples
                        and samples[-1]['policy'] == policy
                        and samples[-1]['repeat'] == repeat + 1
                    ):
                        samples[-1]['close_seconds'] = time.perf_counter() - started
    save(
        args.output,
        dict(
            format='desktop-control-baseline-v2',
            fixture_sha256=meta['sha256'],
            qt_platform=os.environ['QT_QPA_PLATFORM'],
            samples=samples,
            note='Acknowledgement samples, not a renderer paint latency or established SLO.',
            summary={
                policy: {
                    key: dict(
                        p50=percentile([s[key] for s in samples if s['policy'] == policy], 0.5),
                        p95=percentile([s[key] for s in samples if s['policy'] == policy], 0.95),
                    )
                    for key in ('pause_seconds', 'stop_seconds', 'close_seconds')
                }
                for policy in ('human', 'rule')
            },
        ),
    )


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--timeout', type=float, default=120)
    parser.add_argument('--at-fraction', type=float, default=0.1)
    args = parser.parse_args()
    if args.repeats < 1 or args.timeout <= 0 or not 0 < args.at_fraction < 1:
        parser.error('invalid measurement options')
    if args.output.exists():
        parser.error('output already exists')
    main(args)
