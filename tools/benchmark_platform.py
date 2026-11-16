"""Measure the current platform without changing its execution semantics.

Each trial runs in a fresh child process and uses a temporary account database.
The real-data fixture is a validated PREFIX, with an artificial end marker:
it retains all preceding frames so that the native book can be reconstructed.
Preparation is excluded from playback timings. Qt trials use the actual worker,
queued commands and window rendering, with the offscreen platform.
"""

import argparse
from collections import Counter, defaultdict
from contextlib import contextmanager
from dataclasses import asdict
from functools import wraps
import hashlib
import json
import os
from pathlib import Path
import platform
import random

try:
    import resource  # POSIX only; peak memory is not reported on Windows.
except ImportError:
    resource = None  # type: ignore[assignment]
import statistics
import struct
import subprocess
import sys
from tempfile import TemporaryDirectory
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
CASES = (
    'native',
    'market',
    'execution_idle',
    'human_idle',
    'human_trade',
    'rule_idle',
    'rule_trade',
    'desktop_idle',
    'desktop_trade',
)


def sha256(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def save(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(path)


def percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, min(len(ordered) - 1, int(len(ordered) * fraction + 0.999999) - 1))]


def prepare_fixture(source, destination, seconds, symbol, start_ns=None):
    """C++ validates/applies frames; Python only copies their framing verbatim."""
    from sim_platform.market.native import NativeEngine

    before = source.stat()
    engine = NativeEngine(symbol)
    engine.open_file(source, True)
    origin = None
    book = None
    copied = 0
    previous_ns = None
    started = time.perf_counter()
    try:
        with source.open('rb') as stream, destination.open('wb') as output:
            while True:
                candidate = engine.peek_file()
                if candidate is None:
                    break
                stamp = candidate['timestamp_ns']
                if previous_ns is not None and stamp < previous_ns:
                    raise ValueError('source time moved backwards')
                if origin and stamp > origin['time_ns'] + int(seconds * 1e9):
                    break
                result = engine.next_file()
                prefix = stream.read(2)
                if len(prefix) != 2:
                    raise ValueError('copy stream ended before native reader')
                length = int.from_bytes(prefix, 'big')
                payload = stream.read(length)
                if len(payload) != length or not length:
                    raise ValueError('copy stream framing differs from native reader')
                output.write(prefix + payload)
                copied += 1
                previous_ns = stamp
                if result['book']:
                    book = result['book']
                if (
                    origin is None
                    and book
                    and book['bids']
                    and book['asks']
                    and (start_ns is None or stamp >= start_ns)
                ):
                    origin = dict(sequence=copied, time_ns=stamp)
                if copied % 2_000_000 == 0:
                    print(f'Preparing prefix: {copied:,} frames', flush=True)
            output.write(b'\0\0')
    finally:
        engine.close()
    after = source.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError('source changed while preparing fixture')
    if not origin or copied <= origin['sequence']:
        raise ValueError('source has no measurable interval after a two-sided book')
    return dict(
        kind='real-data-prefix',
        origin=origin,
        final_sequence=copied,
        end_time_ns=previous_ns,
        playback_frames=copied - origin['sequence'],
        artificial_end_marker=True,
        requested_seconds=seconds,
        preparation_seconds=time.perf_counter() - started,
        sha256=sha256(destination),
        bytes=destination.stat().st_size,
    )


def synthetic_fixture(path, frames, seconds):
    """Constant-size book: directory, bid/ask, then repeated replacement messages."""
    stock = b'AAPL    '
    start = 34_200_000_000_000

    def header(kind, stamp):
        return kind + struct.pack('>HH', 7, 0) + stamp.to_bytes(6, 'big')

    def write(stream, payload):
        stream.write(struct.pack('>H', len(payload)) + payload)

    with path.open('wb') as stream:
        write(
            stream,
            header(b'R', start)
            + stock
            + b'QN'
            + struct.pack('>I', 100)
            + b'NC  PNN1N'
            + struct.pack('>I', 1)
            + b'N',
        )
        for order, side, price in ((1, b'B', 1_900_000), (2, b'S', 1_901_000)):
            write(
                stream,
                header(b'A', start + order)
                + struct.pack('>Q', order)
                + side
                + struct.pack('>I', 100)
                + stock
                + struct.pack('>I', price),
            )
        previous = 2
        for index in range(1, frames + 1):
            stamp = start + 2 + int(index * seconds * 1e9 / frames)
            current = index + 2
            write(
                stream,
                header(b'U', stamp) + struct.pack('>QQII', previous, current, 100, 1_901_000),
            )
            previous = current
        stream.write(b'\0\0')
    return dict(
        kind='synthetic-constant-book',
        origin=dict(sequence=3, time_ns=start + 2),
        final_sequence=frames + 3,
        end_time_ns=start + 2 + int(seconds * 1e9),
        playback_frames=frames,
        artificial_end_marker=True,
        requested_seconds=seconds,
        sha256=sha256(path),
        bytes=path.stat().st_size,
    )


class Timings:
    """Opt-in, trial-local instrumentation; exclusive totals avoid double counting."""

    def __init__(self):
        self.enabled = False
        self.local = threading.local()
        self.totals = defaultdict(lambda: [0, 0, 0])
        self.patches = []

    @contextmanager
    def span(self, name):
        if not self.enabled:
            yield
            return
        stack = getattr(self.local, 'stack', None)
        if stack is None:
            stack = self.local.stack = []
        item = [time.perf_counter_ns(), 0]
        stack.append(item)
        try:
            yield
        finally:
            elapsed = time.perf_counter_ns() - item[0]
            stack.pop()
            if stack:
                stack[-1][1] += elapsed
            total = self.totals[name]
            total[0] += 1
            total[1] += elapsed
            total[2] += elapsed - item[1]

    def wrap(self, owner, attribute, name):
        original = getattr(owner, attribute)

        @wraps(original)
        def measured(*args, **kwargs):
            with self.span(name):
                return original(*args, **kwargs)

        self.patches.append((owner, attribute, original))
        setattr(owner, attribute, measured)

    def install(self, desktop=False):
        from sim_platform.market.native import NativeEngine
        from sim_platform.market.session import MarketSession
        from sim_platform.execution.service import ExecutionService
        from sim_platform.decisions.policy.runner import PolicyDecisionRunner
        from sim_platform.decisions.policy import invocation
        from sim_platform.persistence.sql.database import Database
        from sim_platform.research.controller import ResearchController
        from sim_platform.persistence.workspace.market_cache import MarketCacheFiles
        from sqlalchemy.pool import Pool
        from sqlalchemy.engine import Connection

        for method in ('peek_file', 'next_file'):
            self.wrap(NativeEngine, method, 'bridge.' + method)
        self.wrap(NativeEngine, '_decode', 'bridge.json_decode')
        self.wrap(MarketSession, '_advance', 'market.advance')
        self.wrap(ExecutionService, 'advance', 'execution.advance')
        self.wrap(PolicyDecisionRunner, 'observe', 'decision.observe')
        self.wrap(PolicyDecisionRunner, '_decide', 'decision.complete')
        self.wrap(invocation, 'invoke', 'decision.invoke_validate')
        self.wrap(ResearchController, 'view', 'presentation.view')
        self.wrap(Pool, 'connect', 'database.acquire_connection')
        self.wrap(Connection, '_commit_impl', 'database.commit')
        self.wrap(Connection, '_exec_single_context', 'database.sql_execute')
        for method in ('append', 'read', 'write'):
            self.wrap(MarketCacheFiles, method, 'history.' + method)
        original = Database.unit_of_work

        @contextmanager
        def measured(database, *, write=False):
            with self.span('database.write_uow' if write else 'database.read_uow'):
                with original(database, write=write) as session:
                    yield session

        self.patches.append((Database, 'unit_of_work', original))
        Database.unit_of_work = measured
        if desktop:
            from sim_platform.desktop.web.window import WebResearchWindow

            self.wrap(WebResearchWindow, 'publish', 'presentation.qt_render')

    def close(self):
        self.enabled = False
        for owner, attribute, original in reversed(self.patches):
            setattr(owner, attribute, original)

    def result(self):
        return {
            name: dict(
                calls=value[0], inclusive_seconds=value[1] / 1e9, exclusive_seconds=value[2] / 1e9
            )
            for name, value in sorted(self.totals.items())
        }


class IdlePolicy:
    kind, name, version = 'rule', 'benchmark-no-actions', '1'
    requires = ('book', 'account')
    parameters = {}

    def decide(self, observation, state, timeout_ms):
        return dict(actions=[], state=state)


def canonical(market, account=None):
    value = {key: market[key] for key in ('sequence', 'time_ns', 'bids', 'asks')}
    if account is not None:
        value['account'] = {
            key: account[key] for key in ('cash', 'fees', 'realized_pnl', 'positions')
        }
        value['account']['orders'] = [
            {key: order[key] for key in ('side', 'quantity', 'limit_price', 'filled', 'status')}
            for order in account['orders']
        ]
    encoded = json.dumps(value, sort_keys=True, separators=(',', ':'))
    return dict(data=value, sha256=hashlib.sha256(encoded.encode()).hexdigest())


class Progress:
    def __init__(self, origin):
        self.origin = origin
        self.bins = Counter()
        self.boundaries = []
        self.last_second = 0
        self.started = time.perf_counter()

    def record(self, stamp, count=1):
        second = max(0, (stamp - self.origin['time_ns']) // 1_000_000_000)
        self.bins[second] += count
        if second > self.last_second:
            now = time.perf_counter() - self.started
            self.boundaries.append(dict(simulated_second=second, elapsed_seconds=now))
            self.last_second = second


def core_trial(args, directory, timings):
    from sim_platform import Platform
    from sim_platform.market.native import NativeEngine
    from sim_platform.decisions import DecisionConfig
    from sim_platform.execution import ExecutionConfig

    meta = json.loads(args.fixture_meta.read_text())
    origin = meta['origin']
    started = time.perf_counter()
    owner = None
    platform_root = None
    controller = None
    native = None
    preparation = None
    setup = None
    try:
        if args.worker == 'native':
            native = NativeEngine(args.symbol)
            native.open_file(args.input)
            book = None
            result = None
            for _ in range(origin['sequence']):
                result = native.next_file()
                book = result['book'] or book
            preparation = time.perf_counter() - started
            setup = 0
            progress = Progress(origin)
            timings.enabled = args.profile
            while True:
                if native.peek_file() is None:
                    break
                result = native.next_file()
                book = result['book'] or book
                progress.record(result['timestamp_ns'])
            wall = time.perf_counter() - progress.started
            timings.enabled = False
            market = dict(
                sequence=result['sequence'],
                time_ns=result['timestamp_ns'],
                bids=book['bids'],
                asks=book['asks'],
            )
            status = 'completed'
            account = None
            decisions = 0
        else:
            platform_root = Platform(directory)
            controller = platform_root.open_research()
            controller.begin_prepare(
                args.input, symbol=args.symbol, start_mode='custom', start_time_ns=origin['time_ns']
            )
            while controller.preparation['status'] == 'preparing':
                controller.prepare_tick(max_events=5000, budget_seconds=0.035)
            if controller.market.snapshot.sequence != origin['sequence']:
                raise ValueError('prepared origin differs from fixture manifest')
            preparation = time.perf_counter() - started
            bind_started = time.perf_counter()
            if args.worker != 'market':
                profile = platform_root.create_profile('Benchmark')
                account_id = platform_root.create_account(profile, 'Temporary', 100_000_000)
                if args.worker == 'execution_idle':
                    maker = platform_root.create_maker(profile, 'Human', 'human', '1')
                    run = platform_root.create_run(
                        profile,
                        account_id,
                        maker,
                        [args.symbol],
                        dict(
                            execution=asdict(ExecutionConfig()), research_source=controller.source
                        ),
                    )
                    owner = platform_root.execution_session(profile, run, maker, controller.market)
                elif args.worker == 'rule_idle':
                    policy = IdlePolicy()
                    maker, run = platform_root.create_managed_decision_run(
                        profile,
                        account_id,
                        args.symbol,
                        policy,
                        DecisionConfig(),
                        ExecutionConfig(),
                        source=controller.source,
                    )
                    controller.profile, controller.run_id = profile, run
                    controller.runner = platform_root.decision_runner(
                        profile, run, maker, controller.market, policy
                    )
                    owner = controller.runner
                else:
                    controller.start(
                        profile,
                        account_id,
                        policy='rule' if args.worker == 'rule_trade' else 'human',
                        execution_mode='controlled',
                    )
                    owner = controller.runner
                    run = controller.run_id
                    if args.worker == 'human_trade':
                        controller.order(
                            'benchmark-buy', 'buy', 10, controller.market.snapshot.asks[0][0]
                        )
            else:
                owner = controller.market
            setup = time.perf_counter() - bind_started
            owner.play()
            progress = Progress(origin)
            timings.enabled = args.profile
            while controller.market.status == 'playing' and not getattr(owner, 'closed', False):
                events = owner.pump(1)
                if not events:
                    break
                event = events[0]
                if event.kind in ('market', 'clock'):
                    progress.record(event.time_ns)
                if event.snapshot.status == 'failed':
                    raise ValueError(event.message or 'market failed')
            wall = time.perf_counter() - progress.started
            timings.enabled = False
            market = asdict(controller.market.snapshot)
            account = (
                asdict(platform_root.snapshot(profile, account_id))
                if args.worker != 'market'
                else None
            )
            status = (
                platform_root.run(profile, run, include_records=False)['status']
                if args.worker != 'market'
                else market['status']
            )
            decisions = (
                len(platform_root.decision_records(profile, run)) if controller.runner else 0
            )
        if status != 'completed':
            raise ValueError('trial did not complete: ' + status)
        return dict(
            wall_seconds=wall,
            preparation_seconds=preparation,
            setup_seconds=setup,
            origin=origin,
            final_sequence=market['sequence'],
            end_time_ns=market['time_ns'],
            frames=market['sequence'] - origin['sequence'],
            decisions=decisions,
            final=canonical(market, account),
            status=status,
            source_second_density=dict(progress.bins),
            boundary_timings=progress.boundaries,
        )
    finally:
        timings.enabled = False
        close_started = time.perf_counter()
        if owner is not None and args.worker == 'execution_idle':
            owner.close()
        if platform_root is not None:
            platform_root.close()
        if native is not None:
            native.close()
        timings.cleanup_seconds = time.perf_counter() - close_started


def desktop_trial(args, directory, timings):
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtCore import QTimer
    from tools.desktop.driver import DesktopDriver

    meta = json.loads(args.fixture_meta.read_text())
    origin = meta['origin']
    driver = DesktopDriver(directory, (args.input, None), timeout=args.timeout)
    window = driver.window
    until = driver.until
    setup_started = time.perf_counter()
    try:
        driver.prepare(
            args.input,
            policy='rule' if args.worker == 'desktop_trade' else 'human',
            start_ns=origin['time_ns'],
            symbol=args.symbol,
        )
        preparation = time.perf_counter() - setup_started
        setup = 0.0
        if driver.view['market']['sequence'] != origin['sequence']:
            raise ValueError('desktop prepared origin differs')
        frame_times = []
        heartbeat_delays = []
        state = dict(last_heartbeat=time.perf_counter(), previous_sequence=origin['sequence'])

        def heartbeat():
            now = time.perf_counter()
            heartbeat_delays.append(max(0, now - state['last_heartbeat'] - 0.01))
            state['last_heartbeat'] = now

        timer = QTimer()
        timer.setInterval(10)
        timer.timeout.connect(heartbeat)

        def published(view):
            if view.get('market') and view['market']['sequence'] > state['previous_sequence']:
                frame_times.append(time.perf_counter())
                state['previous_sequence'] = view['market']['sequence']

        window.owners[driver.identity].event.connect(
            lambda kind, value: published(value['value']) if kind == 'view' else None
        )
        timer.start()
        timings.enabled = args.profile
        started = time.perf_counter()
        driver.operation('play', speed=None)
        until(lambda: driver.view['execution']['status'] != 'running')
        wall = time.perf_counter() - started
        timings.enabled = False
        timer.stop()
        view = driver.view
        if view['execution']['status'] != 'completed':
            raise ValueError('desktop run did not complete')
        intervals = [b - a for a, b in zip(frame_times, frame_times[1:])]
        return dict(
            wall_seconds=wall,
            preparation_seconds=preparation,
            setup_seconds=setup,
            origin=origin,
            final_sequence=view['market']['sequence'],
            end_time_ns=view['market']['time_ns'],
            frames=view['market']['sequence'] - origin['sequence'],
            decisions=len(view['decisions']),
            decisions_count_is_tail=True,
            final=canonical(view['market'], view['account']),
            status='completed',
            qt=dict(
                platform='offscreen',
                updates=len(frame_times),
                update_interval_p50_seconds=percentile(intervals, 0.5),
                update_interval_p95_seconds=percentile(intervals, 0.95),
                heartbeat_lateness_p95_seconds=percentile(heartbeat_delays, 0.95),
            ),
        )
    finally:
        timings.enabled = False
        close_started = time.perf_counter()
        driver.close()
        timings.cleanup_seconds = time.perf_counter() - close_started


def worker(args):
    timings = Timings()
    started = time.perf_counter()
    result = dict(case=args.worker, instrumented=args.profile)
    try:
        if args.profile:
            timings.install(desktop=args.worker.startswith('desktop'))
        with TemporaryDirectory(prefix='platform-benchmark-') as temporary:
            directory = Path(temporary) / 'accounts'
            function = desktop_trial if args.worker.startswith('desktop') else core_trial
            result.update(function(args, directory, timings))
            result['database_bytes_after_close'] = (
                sum(p.stat().st_size for p in directory.rglob('*') if p.is_file())
                if directory.exists()
                else 0
            )
        seconds = (result['end_time_ns'] - result['origin']['time_ns']) / 1e9
        result.update(
            simulated_seconds=seconds,
            speed_x=seconds / result['wall_seconds'],
            frames_per_second=result['frames'] / result['wall_seconds'],
            cleanup_seconds=timings.cleanup_seconds,
        )
    except Exception as exc:
        result.update(status='failed', error=f'{type(exc).__name__}: {exc}')
        import traceback

        result['traceback'] = traceback.format_exc()
    finally:
        timings.close()
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss if resource else None
    result.update(
        process_seconds=time.perf_counter() - started,
        peak_rss_bytes=(None if rss is None else rss if sys.platform == 'darwin' else rss * 1024),
        timings=timings.result(),
    )
    save(args.result, result)
    return 0 if result['status'] == 'completed' else 1


def aggregate(samples):
    rows = []
    for case in CASES:
        trials = [s for s in samples if s['case'] == case and not s['instrumented']]
        if not trials:
            continue
        valid = [s for s in trials if s['status'] == 'completed']
        row = dict(case=case, completed=len(valid), failed=len(trials) - len(valid))
        if valid:
            for key in (
                'wall_seconds',
                'preparation_seconds',
                'speed_x',
                'frames_per_second',
                'peak_rss_bytes',
                'database_bytes_after_close',
                'cleanup_seconds',
            ):
                row[key] = statistics.median(s[key] for s in valid)
            row.update(
                min_wall_seconds=min(s['wall_seconds'] for s in valid),
                max_wall_seconds=max(s['wall_seconds'] for s in valid),
                deterministic=len({s['final']['sha256'] for s in valid}) == 1,
                frames=valid[0]['frames'],
                simulated_seconds=valid[0]['simulated_seconds'],
            )
        rows.append(row)
    return rows


def markdown(report):
    rows = [
        '# Current platform performance baseline',
        '',
        'Unpaced throughput; 1× pacing is not implemented by this benchmark. Preparation is excluded.',
        'Fresh subprocess/database for every trial; file cache is uncontrolled (not a cold-cache benchmark).',
        'Qt uses actual window/worker/queued signals with offscreen rendering; physical display latency is unmeasured.',
        '',
        '| Case | Trials | Frames | Simulated s | Playback s (median) | Speed | Frames/s | Peak RSS MiB | DB MiB |',
        '| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |',
    ]
    for row in report['summary']:
        if row['completed']:
            rows.append(
                f"| {row['case']} | {row['completed']} | {row['frames']:,} | {row['simulated_seconds']:.3f} | "
                f"{row['wall_seconds']:.3f} | {row['speed_x']:.3f}× | {row['frames_per_second']:.0f} | "
                f"{row['peak_rss_bytes']/2**20:.1f} | {row['database_bytes_after_close']/2**20:.2f} |"
            )
        else:
            rows.append(f"| {row['case']} | FAILED | | | | | | | |")
    rows += [
        '',
        '## Diagnostic instrumentation',
        '',
        'Separate instrumented trials, excluded from the throughput medians. Exclusive times do not double count nested calls.',
        '',
    ]
    for sample in report['samples']:
        if not sample['instrumented'] or sample['status'] != 'completed':
            continue
        rows += [
            f"### {sample['case']}",
            '',
            '| Component | Calls | Inclusive s | Exclusive s |',
            '| --- | ---: | ---: | ---: |',
        ]
        for name, value in sorted(
            sample['timings'].items(), key=lambda item: -item[1]['exclusive_seconds']
        ):
            rows.append(
                f"| {name} | {value['calls']:,} | {value['inclusive_seconds']:.3f} | {value['exclusive_seconds']:.3f} |"
            )
        rows.append('')
    rows += [
        '## Scope and checks',
        '',
        '- Source prefix includes warm-up frames and an artificial terminator; this is not a full-day result.',
        '- End-of-prefix closes remaining orders normally. Trade outcomes are model/fixture results, not market recommendations.',
        '- RSS includes initialization/preparation/final result collection; it is not steady-state book memory.',
        '- Native includes C++ calls and JSON decoding. It is not C++-only throughput.',
        '- UI heartbeat measures main-thread scheduling delay; it is not queued Pause/Stop acknowledgement.',
        '- Stop/cancel response and full-day scalability require additional probes.',
        f"- Same-case deterministic results: {all(r.get('deterministic', False) for r in report['summary'])}.",
        f"- Cross-layer final-state checks: `{json.dumps(report.get('checks', {}), sort_keys=True)}`.",
        '',
    ]
    return '\n'.join(rows)


def main(args):
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    fixture = args.output / 'prefix.bin'
    preparation_started = time.perf_counter()
    source = args.input.resolve() if args.input else None
    if source:
        source_before = source.stat()
        meta = prepare_fixture(source, fixture, args.seconds, args.symbol, args.start_time_ns)
        hash_started = time.perf_counter()
        identity = dict(
            path=str(source),
            bytes=source_before.st_size,
            sha256=sha256(source),
            hash_seconds=time.perf_counter() - hash_started,
        )
        source_after = source.stat()
        if (source_before.st_size, source_before.st_mtime_ns) != (
            source_after.st_size,
            source_after.st_mtime_ns,
        ):
            raise ValueError('source changed during fixture/hash preparation')
    else:
        meta = synthetic_fixture(fixture, args.frames, args.seconds)
        identity = dict(kind='generated-synthetic')
    manifest = args.output / 'fixture.json'
    save(manifest, meta)
    report = dict(
        format='platform-benchmark-v1',
        source=identity,
        fixture=meta,
        environment=dict(
            python=sys.version,
            executable=sys.executable,
            os=platform.platform(),
            machine=platform.machine(),
            logical_cpus=os.cpu_count(),
            qt_platform='offscreen',
            cache='uncontrolled; no cache flush',
            git_head=subprocess.check_output(
                ['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True
            ).strip(),
            native_sha256=(
                sha256(ROOT / 'build/libitch_market.dylib')
                if sys.platform == 'darwin'
                else sha256(ROOT / 'build/libitch_market.so')
            ),
            source_files_sha256={
                str(p.relative_to(ROOT)): sha256(p)
                for parent, pattern in (
                    (ROOT / 'sim_platform', '*.py'),
                    (ROOT / 'src', '*.cpp'),
                    (ROOT / 'include', '*.hpp'),
                )
                for p in sorted(parent.rglob(pattern))
            },
        ),
        fixture_and_hash_seconds=time.perf_counter() - preparation_started,
        samples=[],
        summary=[],
        checks={},
    )
    print(
        f"Fixture: {meta['playback_frames']:,} measured frames, "
        f"{(meta['end_time_ns']-meta['origin']['time_ns'])/1e9:.3f} simulated seconds",
        flush=True,
    )
    jobs = []
    rng = random.Random(20261004)
    for repeat in range(args.repeats):
        cases = list(args.cases)
        rng.shuffle(cases)
        jobs.extend((case, repeat + 1, False) for case in cases)
    jobs.extend((case, 1, True) for case in args.profiles if case in args.cases)
    for number, (case, repeat, instrumented) in enumerate(jobs, 1):
        result_path = args.output / f'{number:03d}-{case}.json'
        command = [
            sys.executable,
            str(Path(__file__).resolve()),
            '--worker',
            case,
            '--input',
            str(fixture),
            '--fixture-meta',
            str(manifest),
            '--result',
            str(result_path),
            '--symbol',
            args.symbol,
            '--timeout',
            str(args.timeout),
        ]
        if instrumented:
            command.append('--profile')
        env = dict(os.environ, QT_QPA_PLATFORM='offscreen')
        print(
            f'[{number}/{len(jobs)}] {case}, repeat {repeat}'
            + (' (instrumented)' if instrumented else ''),
            flush=True,
        )
        try:
            process = subprocess.run(
                command, cwd=ROOT, env=env, capture_output=True, text=True, timeout=args.timeout
            )
            if result_path.exists():
                sample = json.loads(result_path.read_text())
            else:
                sample = dict(
                    case=case,
                    instrumented=instrumented,
                    status='failed',
                    error=f'child exit {process.returncode}',
                    stderr=process.stderr[-4000:],
                )
        except subprocess.TimeoutExpired:
            sample = dict(
                case=case, instrumented=instrumented, status='failed', error='parent trial timeout'
            )
        sample['repeat'] = repeat
        report['samples'].append(sample)
        report['summary'] = aggregate(report['samples'])
        save(args.output / 'report.json', report)
        if sample['status'] == 'completed':
            print(f"  {sample['wall_seconds']:.3f}s, {sample['speed_x']:.3f}×", flush=True)
        else:
            print('  FAILED: ' + sample['error'], flush=True)
    valid = {
        s['case']: s
        for s in report['samples']
        if s['status'] == 'completed' and not s['instrumented']
    }
    for left, right in (
        ('native', 'market'),
        ('execution_idle', 'human_idle'),
        ('human_idle', 'rule_idle'),
        ('human_idle', 'desktop_idle'),
        ('rule_trade', 'desktop_trade'),
    ):
        if left in valid and right in valid:
            a, b = valid[left], valid[right]
            if left == 'native':
                equal = a['final']['data'] == {
                    k: b['final']['data'][k] for k in ('sequence', 'time_ns', 'bids', 'asks')
                }
            else:
                equal = a['final']['sha256'] == b['final']['sha256']
            report['checks'][left + '=' + right] = equal
    save(args.output / 'report.json', report)
    (args.output / 'report.md').write_text(markdown(report))
    print('Saved ' + str(args.output / 'report.md'), flush=True)
    failed = any(s['status'] != 'completed' for s in report['samples'])
    mismatch = any(not value for value in report['checks'].values())
    nondeterministic = any(not r.get('deterministic', False) for r in report['summary'])
    return 1 if failed or mismatch or nondeterministic else 0


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument(
        '--input', type=Path, help='optional real BinaryFILE; otherwise generate synthetic data'
    )
    result.add_argument('--output', type=Path, default=ROOT / 'build/platform-baseline')
    result.add_argument('--symbol', default='AAPL')
    result.add_argument('--seconds', type=float, default=5)
    result.add_argument(
        '--start-time-ns',
        type=int,
        help='real-data origin at/after this source timestamp, with a two-sided book',
    )
    result.add_argument('--frames', type=int, default=1000, help='synthetic playback frames')
    result.add_argument('--repeats', type=int, default=3)
    result.add_argument('--cases', nargs='+', choices=CASES, default=list(CASES))
    result.add_argument(
        '--profiles', nargs='*', choices=CASES, default=['human_idle', 'rule_trade', 'desktop_idle']
    )
    result.add_argument('--timeout', type=float, default=600)
    result.add_argument('--worker', choices=CASES, help=argparse.SUPPRESS)
    result.add_argument('--fixture-meta', type=Path, help=argparse.SUPPRESS)
    result.add_argument('--result', type=Path, help=argparse.SUPPRESS)
    result.add_argument('--profile', action='store_true', help=argparse.SUPPRESS)
    return result


if __name__ == '__main__':
    argument_parser = parser()
    options = argument_parser.parse_args()
    import math

    if (
        not math.isfinite(options.seconds)
        or options.seconds <= 0
        or options.frames < 1
        or options.repeats < 1
        or not math.isfinite(options.timeout)
        or options.timeout <= 0
    ):
        argument_parser.error('seconds, frames, repeats and timeout must be positive and finite')
    sys.exit(worker(options) if options.worker else main(options))
