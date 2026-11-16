"""Compare the former Python export loop with native batches, then import a full day.

Outputs small JSON evidence; all large generated files live in an owned temporary
workspace and are cleaned on exit. No network, model calls or source modification.
"""

import argparse
import csv
import json
from pathlib import Path
import random
import resource
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sim_platform.market.session import MarketSession
from sim_platform.market.prepared.builder import build
from sim_platform.market.prepared.codec import encode_event, encode_state
from sim_platform.market.prepared.reader import PreparedMarket
from benchmark_prepared_runtime import distribution as milliseconds


def distribution(samples):
    values = milliseconds(samples)
    return dict(
        count=values['count'],
        **{
            key.replace('_ms', '_us'): value * 1000 if value is not None else None
            for key, value in values.items()
            if key != 'count'
        },
    )


def prefix(source, destination, count):
    """Copy complete BinaryFILE frames and add an explicit fixture terminator."""
    with source.open('rb') as incoming, destination.open('wb') as outgoing:
        for _ in range(count):
            header = incoming.read(2)
            if len(header) != 2:
                raise ValueError('source ends before the requested prefix')
            length = int.from_bytes(header, 'big')
            if not length:
                break
            payload = incoming.read(length)
            if len(payload) != length:
                raise ValueError('source has a truncated frame')
            outgoing.write(header + payload)
        outgoing.write(b'\0\0')


def reference_export(source, target):
    """Former per-frame JSON/Python path, retained only as a benchmark reference."""
    market = MarketSession('AAPL')
    market.load(source)
    market.play()
    started = time.perf_counter()
    pump_ns = encode_write_ns = frames = versions = 0
    previous = encode_state(market.snapshot, 5)
    try:
        with (
            (target / 'events.bin').open('wb') as events,
            (target / 'states.bin').open('wb') as states,
        ):
            states.write(previous)
            while True:
                tick = time.perf_counter_ns()
                event = market.pump(1)[0]
                pump_ns += time.perf_counter_ns() - tick
                if market.status == 'completed':
                    break
                if market.status == 'failed':
                    raise RuntimeError(event.message)
                tick = time.perf_counter_ns()
                raw = encode_state(event.snapshot, 5)
                if raw != previous:
                    versions += 1
                    states.write(raw)
                    previous = raw
                events.write(encode_event(event, versions))
                frames += 1
                encode_write_ns += time.perf_counter_ns() - tick
        return dict(
            seconds=time.perf_counter() - started,
            source_frames=frames,
            native_json_python_pump_seconds=pump_ns / 1e9,
            python_encode_write_seconds=encode_write_ns / 1e9,
            artifact_data_bytes=sum(p.stat().st_size for p in target.iterdir()),
            instrumented=True,
        )
    finally:
        market.close()


def queries(reader, count=5000):
    m = reader.manifest
    rng = random.Random(731)
    points = [rng.randint(m['first_ns'], m['last_ns']) for _ in range(count)]
    runs = []
    for _ in range(2):
        samples = []
        for point in points:
            started = time.perf_counter_ns()
            reader.snapshot_at(point)
            samples.append(time.perf_counter_ns() - started)
        runs.append(distribution(samples))
    return dict(
        first=runs[0],
        repeated=runs[1],
        os_cold=False,
        native_calls_per_query=0,
        replayed_frames_per_query=0,
    )


def native_import(source, target, *, implicit=False, profile=False):
    stages = {}
    last_progress = [time.monotonic()]

    def progress(frames):
        if time.monotonic() - last_progress[0] >= 10:
            print('import progress: ' + str(frames) + ' source frames', flush=True)
            last_progress[0] = time.monotonic()

    started = time.perf_counter()
    build(
        source,
        target,
        allow_implicit_eof=implicit,
        timings=stages,
        profile_native=profile,
        progress=progress,
    )
    seconds = time.perf_counter() - started
    opens = []
    for _ in range(5):
        started = time.perf_counter_ns()
        with PreparedMarket(target) as reader:
            opens.append(time.perf_counter_ns() - started)
    with PreparedMarket(target) as reader:
        m = reader.manifest
        return dict(
            seconds=seconds,
            source_bytes=source.stat().st_size,
            source_frames=m['source_frames'],
            retained_events=m['frames'],
            state_versions=m['states'],
            artifact_bytes=sum(p.stat().st_size for p in target.iterdir()),
            open=distribution(opens),
            queries=queries(reader),
            instrumented=profile,
            native_stage_timings=stages,
            lifetime_peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        )


def compare_csv(target, csv_path):
    """All book events against existing pre-change CSV, including depth and source ID."""

    def price(value):
        return f'{value // 10000}.{value % 10000:04d}'

    def levels(values):
        return '|'.join(f'{price(p)}:{q}' for p, q in values)

    count = 0
    with PreparedMarket(target) as reader, csv_path.open(newline='') as stream:
        rows = csv.DictReader(stream)
        for index in range(reader.manifest['frames']):
            event = reader.event(index)
            if event.source_type not in 'AFECXDU':
                continue
            row = next(rows)
            s = event.snapshot
            actual = [
                str(reader.source_sequence(index + 1)),
                str(event.time_ns),
                event.source_type,
                price(s.bids[0][0]) if s.bids else '',
                str(s.bids[0][1]) if s.bids else '',
                price(s.asks[0][0]) if s.asks else '',
                str(s.asks[0][1]) if s.asks else '',
                str(s.bid_levels),
                str(s.ask_levels),
                levels(s.bids),
                levels(s.asks),
            ]
            if actual != list(row.values()):
                raise AssertionError('prepared differs from existing CSV at row ' + str(count + 1))
            count += 1
        if next(rows, None) is not None:
            raise AssertionError('prepared omitted CSV events')
    return dict(
        rows=count,
        all_fields_equal=True,
        note='pre-existing CSV from original native core; not a second independent book',
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--csv', type=Path)
    parser.add_argument('--prefix-frames', type=int, default=1_000_000)
    parser.add_argument('--full-day', action='store_true')
    args = parser.parse_args()
    report = dict(
        symbol='AAPL',
        depth=5,
        source=str(args.source.resolve()),
        cold_cache=False,
        scope='local import feasibility, not live or financial throughput',
    )

    def save():
        args.output.write_text(json.dumps(report, indent=2) + '\n')

    with tempfile.TemporaryDirectory(prefix='prepared-import-') as workspace:
        root = Path(workspace)
        sample = root / 'prefix.bin'
        prefix(args.source, sample, args.prefix_frames)
        old = root / 'reference'
        old.mkdir()
        report['prefix_reference'] = reference_export(sample, old)
        save()
        print('reference prefix complete', flush=True)
        report['prefix_native'] = native_import(sample, root / 'native')
        save()
        print('native prefix complete', flush=True)
        report['prefix_profiled'] = native_import(sample, root / 'profiled', profile=True)
        save()
        if args.full_day:
            report['full_day'] = native_import(args.source, root / 'full', implicit=True)
            save()
            print('full-day import complete', flush=True)
            if args.csv:
                report['full_day']['existing_csv_comparison'] = compare_csv(root / 'full', args.csv)
                save()
                print('full-day CSV comparison complete', flush=True)
    print(json.dumps(report, indent=2), flush=True)


if __name__ == '__main__':
    main()
