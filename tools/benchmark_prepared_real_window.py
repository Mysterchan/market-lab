"""Real-day peak-window performance gate using actual prepared source and financial UOW."""

import argparse
from pathlib import Path
import json
import sys
import tempfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sim_platform import Platform
from sim_platform.experiments.execution.metrics import RuntimeMetrics
from benchmark_prepared_runtime import distribution


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--profile-directory', type=Path)
    args = parser.parse_args()
    profile = json.loads((ROOT / 'tools/fixtures/prepared-runtime.json').read_text())
    report = dict(workload='real-source-window', cases=[], passed=False, complete=False)
    with tempfile.TemporaryDirectory(prefix='prepared-real-window-') as temporary:
        workspace = Path(temporary)
        with Platform(workspace) as app:
            source = app._new_research_market('AAPL')
            started = time.monotonic()
            source.load(args.source, allow_implicit_eof=True)
            while not source.prepare():
                time.sleep(0.01)
            report['prepare_seconds'] = time.monotonic() - started
            metadata = source.reader.manifest
            peak = metadata['history']['peak_second_ns']
            report.update(
                source_frames=metadata['source_frames'],
                retained_events=metadata['frames'],
                peak_second_ns=peak,
                peak_events_per_second=metadata['history']['peak_events_per_second'],
                artifact_bytes=sum(p.stat().st_size for p in source.reader.directory.iterdir()),
                scope='three-second real window starting at the highest one-second related-event count; warm local cache',
            )
            from collections import defaultdict

            regular_density = defaultdict(int)
            for index in range(
                source.reader.prefix(34_200_000_000_000 - 1),
                source.reader.prefix(57_600_000_000_000 - 1),
            ):
                regular_density[source.reader.event_time(index) // 1_000_000_000] += 1
            windows = [('all source', peak, metadata['history']['peak_events_per_second'])]
            if regular_density:
                regular = max(regular_density, key=regular_density.get)
                windows.append(
                    ('regular trading hours', regular * 1_000_000_000, regular_density[regular])
                )
            report['windows'] = [
                dict(name=name, start_ns=stamp, events_per_second=density)
                for name, stamp, density in windows
            ]
            source.close()
            original = app._compose_pipeline

            def bounded(driver, market, **options):
                options.update(start_ns=peak, end_ns=peak + 3_000_000_000)
                return original(driver, market, **options)

            for window, peak, density in windows:
                for trial in range(3):
                    user = app.create_profile('real peak ' + str(trial))
                    account = app.create_account(user, 'cash', 10_000_000_000)
                    with patch.object(app, '_compose_pipeline', bounded):
                        experiment = app.open_experiment(
                            user,
                            account,
                            source=args.source,
                            method='human',
                            settings=dict(
                                start_mode='custom', start_time_ns=peak, allow_implicit_eof=True
                            ),
                            execution_mode='live',
                        )
                    pipeline = experiment.runner.pipeline
                    if args.profile_directory:
                        import cProfile

                        args.profile_directory.mkdir(parents=True, exist_ok=True)
                        run = pipeline._run

                        def profiled(run=run, trial=trial):
                            profiler = cProfile.Profile()
                            try:
                                profiler.runcall(run)
                            finally:
                                profiler.dump_stats(
                                    str(args.profile_directory / ('owner-' + str(trial) + '.stats'))
                                )

                        pipeline._run = profiled
                    delays = []
                    commands = []
                    queries = []

                    class Measured(RuntimeMetrics):
                        def frame(self, target, event):
                            delays.append(max(0, target - event))
                            super().frame(target, event)

                        def span(self, target, event, count):
                            delays.extend([max(0, target - event)] * count)
                            super().span(target, event, count)

                    pipeline.metrics = Measured(100_000_000)
                    experiment.play()
                    start = time.monotonic()
                    next_order = start + 0.1
                    next_read = start + 0.05
                    number = 0
                    while time.monotonic() - start < 3 and experiment.runner.active:
                        now = time.monotonic()
                        if now >= next_order:
                            number += 1
                            next_order += 1
                            began = time.perf_counter_ns()
                            experiment.runner.act(
                                [
                                    dict(
                                        kind='submit',
                                        intent=dict(
                                            client_id='peak-' + str(number),
                                            symbol='AAPL',
                                            side='buy',
                                            quantity=1,
                                            limit_price=1,
                                        ),
                                    )
                                ]
                            )
                            commands.append(time.perf_counter_ns() - began)
                        if now >= next_read:
                            next_read += 0.1
                            began = time.perf_counter_ns()
                            pipeline.reader.snapshot_at(pipeline.clock.target_ns())
                            queries.append(time.perf_counter_ns() - began)
                        time.sleep(0.001)
                    until = time.monotonic() + 3
                    while experiment.runner.active and time.monotonic() < until:
                        time.sleep(0.002)
                    state = pipeline.snapshot()
                    expected = pipeline.reader.prefix(peak + 3_000_000_000 - 1)
                    caught_up = pipeline.driver.sequence == expected
                    began = time.monotonic()
                    experiment.close()
                    shutdown = time.monotonic() - began
                    d, c, q = distribution(delays), distribution(commands), distribution(queries)
                    thresholds = profile['thresholds']
                    passed = (
                        state['state'] == 'completed'
                        and caught_up
                        and d['p99_ms'] <= thresholds['market_processing_delay_p99_ms']
                        and c['p99_ms'] <= thresholds['command_roundtrip_p99_ms']
                        and q['p99_ms'] <= thresholds['market_query_roundtrip_p99_ms']
                        and shutdown <= thresholds['shutdown_seconds_max']
                    )
                    report['cases'].append(
                        dict(
                            window=window,
                            trial=trial + 1,
                            passed=passed,
                            state=state['state'],
                            caught_up=caught_up,
                            market_processing_delay=d,
                            financial_command=c,
                            market_lookup=q,
                            shutdown_seconds=shutdown,
                            error=state['error'],
                        )
                    )
                    args.output.write_text(json.dumps(report, indent=2) + '\n')
                    print(window, trial + 1, passed, flush=True)
    report['complete'] = True
    report['passed'] = all(c['passed'] for c in report['cases'])
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2), flush=True)


if __name__ == '__main__':
    main()
