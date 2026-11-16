"""Short Runtime dispatch probe; scheduling timestamps, not whole-day capacity.

Runs real Runtime children under idle and parent Python CPU load. No model,
SQL, network or new benchmark instrumentation in production hot paths.
"""

import argparse
import json
import platform
from pathlib import Path
import statistics
import multiprocessing
from math import ceil
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sim_platform.experiments.scheduling.policy.controller import LiveWorkPolicy
from sim_platform.experiments.scheduling.host import ExperimentRuntimeHandle


def summary(values):
    values = sorted(values)
    return dict(
        median_ms=statistics.median(values),
        p99_ms=values[ceil(len(values) * 0.99) - 1],
        max_ms=max(values),
    )


def trial(mode, rounds):
    config = dict(
        start_ns=0,
        end_ns=60_000_000_000,
        symbol='AAPL',
        event_buckets=4,
        schedule=dict(
            mode='interval',
            interval_seconds='0.02',
            busy_event_policy='preserve_interval',
            events=[],
        ),
        timeout_seconds='30',
        retry=dict(
            max_attempts=3,
            format_max_attempts=3,
            base_delay_seconds='1',
            max_delay_seconds='8',
            retry_classes=['timeout'],
        ),
    )
    dispatch, delivery = [], []
    with ExperimentRuntimeHandle(LiveWorkPolicy, config) as runtime:
        runtime.clock.resume()
        work = runtime.submit('next_work').result(2)
        for _ in range(rounds):
            runtime.submit('finish', {k: work[k] for k in ('work_id', 'epoch')}).result(2)
            result = runtime.submit('next_work')
            received = []
            result.add_done_callback(lambda _: received.append(runtime.clock.target_ns()))
            if mode == 'parent_cpu':
                deadline = time.perf_counter() + 0.06
                value = 0
                while time.perf_counter() < deadline:
                    value = (value * 3 + 1) % 1_000_003
            work = result.result(2)
            due = int(work['scheduled_due_ns'])
            dispatch.append((int(work['dispatched_ns']) - due) / 1e6)
            # Future completion precedes its callbacks: allow callback adoption.
            deadline = time.monotonic() + 1
            while not received:
                if time.monotonic() >= deadline:
                    raise RuntimeError('delivery timestamp callback did not finish')
                time.sleep(0.001)
            delivery.append((received[0] - due) / 1e6)
    return dict(mode=mode, rounds=rounds, dispatch=summary(dispatch), delivery=summary(delivery))


def direct_agent(client, output, acknowledgement, rounds):
    try:
        work = client.next_work()
        for _ in range(rounds):
            due = client.finish(work)['next_due_ns']
            output.send(dict(due_ns=due))
            work = client.next_work()
            output.send(dict(work=work, received_ns=client.target_ns()))
            acknowledgement.recv()
    finally:
        client.close()
        output.close()
        acknowledgement.close()


def direct_trial(rounds):
    config = dict(
        start_ns=0,
        end_ns=60_000_000_000,
        symbol='AAPL',
        event_buckets=4,
        schedule=dict(
            mode='interval',
            interval_seconds='0.02',
            busy_event_policy='preserve_interval',
            events=[],
        ),
        timeout_seconds='30',
        retry=dict(
            max_attempts=3,
            format_max_attempts=3,
            base_delay_seconds='1',
            max_delay_seconds='8',
            retry_classes=['timeout'],
        ),
    )
    dispatch, delivery = [], []
    context = multiprocessing.get_context('spawn')
    with ExperimentRuntimeHandle(LiveWorkPolicy, config) as runtime:
        runtime.clock.resume()
        client = runtime.work_client()
        read, write = context.Pipe(duplex=False)
        child_ack, parent_ack = context.Pipe(duplex=False)
        child = context.Process(target=direct_agent, args=(client, write, child_ack, rounds))
        child.start()
        write.close()
        child_ack.close()
        try:
            for _ in range(rounds):
                if not read.poll(2):
                    raise RuntimeError('direct agent preparation timed out')
                due = int(read.recv()['due_ns'])
                deadline = time.perf_counter() + 0.06
                value = 0
                while time.perf_counter() < deadline:
                    value = (value * 3 + 1) % 1_000_003
                if not read.poll(2):
                    raise RuntimeError('direct agent work delivery timed out')
                received = read.recv()
                dispatch.append((int(received['work']['dispatched_ns']) - due) / 1e6)
                delivery.append((received['received_ns'] - due) / 1e6)
                parent_ack.send(True)
        finally:
            child.join(1)
            if child.is_alive():
                child.terminate()
                child.join(1)
            client.close()
            read.close()
            parent_ack.close()
        if child.exitcode:
            raise RuntimeError('direct agent child failed')
    return dict(
        mode='parent_cpu_direct_agent',
        rounds=rounds,
        dispatch=summary(dispatch),
        delivery=summary(delivery),
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    results = [trial(mode, 10) for mode in ('idle', 'parent_cpu') for _ in range(3)]
    results.extend(direct_trial(10) for _ in range(3))
    args.output.write_text(
        json.dumps(
            dict(
                python=sys.version,
                machine=platform.machine(),
                os=platform.platform(),
                cases=results,
                limitations='90 short timer samples; nearest-rank p99 with ten samples per trial; '
                'no whole-day, provider or market throughput claim',
            ),
            indent=2,
        )
        + '\n'
    )


if __name__ == '__main__':
    main()
