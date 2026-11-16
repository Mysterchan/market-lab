"""Short native/process load matrix. No real model or account credentials."""

import json
from pathlib import Path
import struct
import sys
import time
from tempfile import TemporaryDirectory
import resource
from functools import partial
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tests/platform'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sim_platform import Platform
from sim_platform.decisions.policy.policies import BuyHoldSell
from sim_platform.decisions.policy.models import DecisionConfig
from sim_platform.execution.models import ExecutionConfig

ORIGIN = 34_200_000_000_000


def fixture(path, spacing, frames=100):
    def common(kind, stamp):
        return kind.encode() + struct.pack('>HH', 7, 0) + stamp.to_bytes(6, 'big')

    def frame(value):
        return struct.pack('>H', len(value)) + value

    records = []
    for n, side in [(1, b'B'), (2, b'S')]:
        records.append(
            frame(
                common('A', ORIGIN)
                + struct.pack('>Q', n)
                + side
                + struct.pack('>I', 100)
                + b'AAPL    '
                + struct.pack('>I', 1_000_000 + n)
            )
        )
    for n in range(frames):
        records.append(
            frame(
                common('P', ORIGIN + (n + 1) * spacing)
                + struct.pack('>Q', 0)
                + b'B'
                + struct.pack('>I', 1)
                + b'AAPL    '
                + struct.pack('>IQ', 1_000_000, n + 1)
            )
        )
    path.write_bytes(b''.join(records) + b'\0\0')
    return path


class SlowRule(BuyHoldSell):
    name = 'slow-rule-probe'

    def decide(self, observation, state, timeout_ms):
        end = time.monotonic() + 0.08
        total = 0
        while time.monotonic() < end:
            total += sum(i * i for i in range(500))
        return super().decide(observation, state, timeout_ms)


def trial(name, spacing, slow=False):
    with TemporaryDirectory() as directory, Platform(Path(directory) / 'db') as app:
        profile = app.create_profile('probe')
        account = app.create_account(profile, 'cash', 100_000_000)
        controller = app.open_research()
        controller.begin_prepare(
            fixture(
                Path(directory) / 'source.bin', spacing, 400 if name == 'dense-slow-json' else 100
            ),
            'AAPL',
            start_mode='first_book',
        )
        while controller.preparation['status'] == 'preparing':
            controller.prepare_tick()
        source = dict(controller.source, execution_mode='live')
        policy = SlowRule() if slow else BuyHoldSell()
        started = time.perf_counter()
        maker, run = app.create_managed_decision_run(
            profile, account, 'AAPL', policy, DecisionConfig(), ExecutionConfig(), source=source
        )
        runner = app._new_live_policy_runner(profile, run, maker, controller.market, policy)
        controller.runner, controller.profile, controller.run_id = runner, profile, run
        setup = time.perf_counter() - started
        if name == 'dense-slow-json':
            original = runner.pipeline.channels.sink

            def slow_json(value):
                time.sleep(0.005)
                original(value)

            runner.pipeline.channels.bind_sink(slow_json)
        if name == 'dense-slow-db':
            original = runner.owner.commands._commit

            def slow_db(*args, **kwargs):
                time.sleep(0.12)
                return original(*args, **kwargs)

            runner.owner.commands._commit = slow_db
        runner.play()
        deadline = time.monotonic() + 5
        while runner.active and time.monotonic() < deadline:
            time.sleep(0.001)
        if runner.active:
            raise RuntimeError('probe deadline exceeded')
        view = runner.pipeline.snapshot()
        started = time.perf_counter()
        close_error = None
        try:
            runner.close()
        except Exception as exc:
            close_error = type(exc).__name__ + ': ' + str(exc)
            controller.runner = None
        close = time.perf_counter() - started
        return dict(
            case=name,
            spacing_ns=spacing,
            setup_ms=setup * 1000,
            close_ms=close * 1000,
            **view,
            close_error=close_error,
            app_peak_rss=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            child_peak_rss=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,
        )


class ProbeFormatter:
    def __init__(self, binding, slow=False, large=False):
        from sim_platform.agents.tools.reads.formatter import ReadFormatter

        self.reader = ReadFormatter(binding)
        self.slow, self.large = slow, large

    def execute(self, captured):
        if self.slow:
            time.sleep(0.08)
        result = self.reader.execute(captured)
        if self.large:
            result['data']['probe_padding'] = 'x' * (256 * 1024)
        return result


def tool_trial(name, slow=False, large=False, burst=False):
    from support.agents import definition, settings
    from sim_platform.agents.tools.worker.host import ToolProcessHost

    with TemporaryDirectory() as directory, Platform(Path(directory) / 'db') as app:
        profile = app.create_profile('probe')
        account = app.create_account(profile, 'cash', 100_000_000)
        app.create_agent(profile, definition())
        config = dict(
            settings(),
            start_ns=str(ORIGIN),
            end_ns=str(ORIGIN + 2_000_000_000),
            lag=dict(threshold_seconds='0.1', window_seconds='0.1', profile_ref='prototype-100ms'),
        )
        formatter = partial(ProbeFormatter, slow=slow, large=large)
        started = time.perf_counter()
        with patch(
            'sim_platform.agents.tools.server.router.ToolProcessHost',
            partial(ToolProcessHost, formatter_factory=formatter),
        ):
            run = app.open_experiment(
                profile,
                account,
                source=fixture(Path(directory) / 'source.bin', 1_000_000, 400),
                method='agent',
                agent_id='demo',
                revision=1,
                settings=config,
                execution_mode='live',
            )
        setup = time.perf_counter() - started
        run.play()
        work = run.runner.next_work().result(2)['result']

        def query(index):
            request = dict(
                run_id=run.run_id,
                work_id=work['work_id'],
                epoch=work['epoch'],
                request_id='probe-' + str(index),
                tool='market.quote',
                arguments=dict(symbol='AAPL'),
            )
            start = time.perf_counter()
            try:
                response = run.request_tool(request).result(3)['result']
                return dict(
                    status=response['status'],
                    elapsed_ms=(time.perf_counter() - start) * 1000,
                    cutoff=response.get('cutoff'),
                )
            except Exception as exc:
                return dict(
                    status=type(exc).__name__, elapsed_ms=(time.perf_counter() - start) * 1000
                )

        with ThreadPoolExecutor(max_workers=8 if burst else 1) as workers:
            requests = list(workers.map(query, range(24 if burst else 3)))
        deadline = time.monotonic() + 5
        while run.runner.active and time.monotonic() < deadline:
            time.sleep(0.001)
        view = run.runner.pipeline.snapshot()
        started = time.perf_counter()
        run.stop()
        close = time.perf_counter() - started
        return dict(
            case=name,
            setup_ms=setup * 1000,
            close_ms=close * 1000,
            requests=requests,
            **view,
            app_peak_rss=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            child_peak_rss=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,
        )


if __name__ == '__main__':
    values = [
        trial(name, spacing, slow)
        for name, spacing, slow in [
            ('sparse-rule', 3_000_000, False),
            ('dense-rule', 1_000_000, False),
            ('dense-slow-rule', 1_000_000, True),
            ('dense-slow-json', 1_000_000, False),
            ('dense-slow-db', 1_000_000, False),
        ]
    ]
    values.extend(
        [
            tool_trial('dense-slow-query', slow=True),
            tool_trial('dense-large-result', large=True),
            tool_trial('dense-burst', burst=True),
        ]
    )
    result = dict(
        cases=values,
        scope='eight short synthetic native trials, including deliberately overloaded failure cases; RSS bytes are lifetime process high-water, not per-run incremental; instrumentation counted only frame metric updates; no public ranking profile',
    )
    destination = Path(__file__).resolve().parents[1] / 'build/benchmarks/live-experiments.json'
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
