"""Prepared-runtime baseline and Agent integration performance gate; actual Platform/Runtime/financial UOW.

Uses a fixed predeclared profile, bounded synthetic source, mock/local IO only.
Deployment evidence combines these results with real-source and regression gates.
"""

from pathlib import Path
import argparse
import asyncio
import multiprocessing
import json
import statistics
import sys
import tempfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
sys.path.insert(0, str(ROOT / 'tests' / 'platform'))
from itch_fixtures import directory, add
from sim_platform import Platform


def distribution(values):
    if not values:
        return dict(count=0, median_ms=None, p99_ms=None, max_ms=None)
    values = sorted(values)
    return dict(
        count=len(values),
        median_ms=statistics.median(values) / 1e6,
        p99_ms=values[min(len(values) - 1, int(0.99 * len(values)))] / 1e6,
        max_ms=max(values) / 1e6,
    )


def fixture(path, profile):
    period = 1_000_000_000 // profile['source_events_per_second']
    count = profile['case_seconds'] * profile['source_events_per_second']
    with path.open('wb') as output:
        output.write(directory('AAPL', timestamp=1))
        output.write(add(1, 'B', 100, 99, 'AAPL', timestamp=2))
        output.write(add(2, 'S', 100, 101, 'AAPL', timestamp=3))
        for index in range(count):
            output.write(add(3 + index, 'S', 1, 101, 'AAPL', timestamp=3 + (index + 1) * period))
        output.write(b'\0\0')
    return path


def run_case(workspace, source, profile, case, trial):
    from sim_platform.experiments.execution.metrics import RuntimeMetrics

    with Platform(workspace) as app:
        user = app.create_profile('runtime gate')
        account = app.create_account(user, 'cash', 100_000_000)
        experiment = app.open_experiment(user, account, source=source, execution_mode='live')
        pipeline = experiment.runner.pipeline
        frames, commands, queue_wait = [], [], []

        class Measured(RuntimeMetrics):
            def frame(self, target, event):
                frames.append(max(0, target - event))
                super().frame(target, event)

            def span(self, target, event, count):
                frames.extend([max(0, target - event)] * count)
                super().span(target, event, count)

        pipeline.metrics = Measured(100_000_000)
        original = pipeline.operation

        def timed(request, position):
            queue_wait.append(max(0, pipeline.clock.target_ns() - int(position['time_ns'])))
            return original(request, position)

        pipeline.operation = timed
        queries = []
        experiment.play()
        start = time.monotonic()
        next_order = start + 0.1
        next_read = start + 0.05
        deadline = start + profile['case_seconds']
        number = 0
        while time.monotonic() < deadline and experiment.runner.active:
            now = time.monotonic()
            if case != 'idle' and now >= next_order:
                number += 1
                next_order += 1 / profile['orders_per_second']
                begun = time.perf_counter_ns()
                experiment.runner.act(
                    [
                        dict(
                            kind='submit',
                            intent=dict(
                                client_id='gate-' + str(number),
                                symbol='AAPL',
                                side='buy',
                                quantity=1,
                                limit_price=101 if case == 'fills_and_reads' else 1,
                            ),
                        )
                    ]
                )
                commands.append(time.perf_counter_ns() - begun)
            if case != 'idle' and now >= next_read:
                next_read += 1 / profile['market_queries_per_second']
                begun = time.perf_counter_ns()
                cutoff = min(pipeline.clock.target_ns(), pipeline.reader.manifest['last_ns'])
                pipeline.reader.snapshot_at(cutoff)
                queries.append(time.perf_counter_ns() - begun)
            time.sleep(0.001)
        until = time.monotonic() + 2
        while experiment.runner.active and time.monotonic() < until:
            time.sleep(0.002)
        state = pipeline.snapshot()
        begun = time.monotonic()
        experiment.close()
        shutdown = time.monotonic() - begun
        thresholds = profile['thresholds']
        delay = distribution(frames)
        command = distribution(commands)
        query = distribution(queries)
        complete = pipeline.driver.sequence == pipeline.reader.manifest['frames']
        passed = (
            complete
            and state['state'] == 'completed'
            and delay['p99_ms'] <= thresholds['market_processing_delay_p99_ms']
            and (not commands or command['p99_ms'] <= thresholds['command_roundtrip_p99_ms'])
            and (not queries or query['p99_ms'] <= thresholds['market_query_roundtrip_p99_ms'])
            and shutdown <= thresholds['shutdown_seconds_max']
        )
        return dict(
            case=case,
            trial=trial,
            passed=passed,
            completed=complete,
            state=state['state'],
            financial_commands=command,
            market_lookup=query,
            owner_queue_wait=distribution(queue_wait),
            market_processing_delay=delay,
            shutdown_seconds=shutdown,
            query_scope='trusted indexed lookup, not complete Tools IPC; Agent tool roundtrip is a separate gate',
        )


class ParsingProvider:
    async def request_received(self, profile, payload, secret, received):
        await asyncio.sleep(0.02)
        received()
        # CPU parsing/load is isolated in Agent, not sleeping on the App GIL.
        deadline = time.monotonic() + 0.2
        value = 1
        while time.monotonic() < deadline:
            value = (value * 1664525 + 1013904223) % 2**32
        return dict(output=[], sample='x' * 50000)


class ModelWorkload:
    def __init__(self, finished):
        self.finished = finished

    def __call__(self, client, configuration):
        work = client.next_work()
        result = client.request_model(work, {'input': 'local performance fixture'})
        if result['output'] != []:
            raise RuntimeError('unexpected fixture model result')
        self.finished.value = 1
        client.wait_stopped()


def agent_case(workspace, source, profile, trial):
    from functools import partial
    from support.agents import definition, settings
    from support.tools import request
    from sim_platform.agents.hosting.host import AgentProcessHost
    from sim_platform.models.contracts import ModelProfile
    import threading

    finished = multiprocessing.get_context('spawn').RawValue('b', 0)
    with Platform(workspace) as app:
        user = app.create_profile('Agent gate')
        account = app.create_account(user, 'cash', 100_000_000)
        app.create_agent(user, definition())
        config = dict(
            settings(),
            start_ns='3',
            end_ns=str(3 + profile['case_seconds'] * 1_000_000_000),
            timeout_seconds='2',
            granted_tools=['market.quote', 'orders.submit_limit'],
        )
        with patch(
            'sim_platform.agents.hosting.host.AgentProcessHost',
            partial(AgentProcessHost, program=ModelWorkload(finished)),
        ):
            experiment = app.open_experiment(
                user,
                account,
                source=source,
                method='agent',
                agent_id='demo',
                revision=1,
                settings=config,
                execution_mode='live',
            )
        app.set_model_credential('gate', 'local-fixture-not-an-api-key')
        app.configure_experiment_model(
            experiment,
            ModelProfile('openai-responses', 'mock', 'gate'),
            provider=ParsingProvider(),
        )
        pipeline = experiment.runner.pipeline
        from sim_platform.experiments.execution.metrics import RuntimeMetrics

        delays = []

        class Measured(RuntimeMetrics):
            def frame(self, target, event):
                delays.append(max(0, target - event))
                super().frame(target, event)

            def span(self, target, event, count):
                delays.extend([max(0, target - event)] * count)
                super().span(target, event, count)

        pipeline.metrics = Measured(100_000_000)
        queries = []
        commands = []
        number = 0
        experiment.play()
        start = time.monotonic()
        next_read = start + 0.05
        next_order = start + 0.1
        while time.monotonic() - start < profile['case_seconds'] and experiment.runner.active:
            work = experiment.runner._tools.controls.snapshot()
            if work['state'] != 'running':
                time.sleep(0.001)
                continue
            now = time.monotonic()
            if now >= next_read:
                number += 1
                next_read += 1 / profile['market_queries_per_second']
                value = request(
                    'market.quote',
                    {'symbol': 'AAPL'},
                    number='read-' + str(number),
                    run=experiment.run_id,
                )
                value.update(work_id=work['work_id'], epoch=work['epoch'])
                begun = time.perf_counter_ns()
                response = experiment.request_tool(value).result(2)['result']
                if response['status'] != 'ok':
                    raise RuntimeError('query failed')
                queries.append(time.perf_counter_ns() - begun)
            if now >= next_order:
                number += 1
                next_order += 1 / profile['orders_per_second']
                value = request(
                    'orders.submit_limit',
                    dict(symbol='AAPL', side='buy', quantity_shares=1, limit_price_usd='0.0001'),
                    number='order-' + str(number),
                    run=experiment.run_id,
                )
                value.update(work_id=work['work_id'], epoch=work['epoch'])
                begun = time.perf_counter_ns()
                response = experiment.request_tool(value).result(2)['result']
                if response['status'] != 'ok':
                    raise RuntimeError('order failed')
                commands.append(time.perf_counter_ns() - begun)
            time.sleep(0.001)
        until = time.monotonic() + 2
        while experiment.runner.active and time.monotonic() < until:
            time.sleep(0.002)
        state = pipeline.snapshot()
        begun = time.monotonic()
        experiment.close()
        shutdown = time.monotonic() - begun
        q = distribution(queries)
        c = distribution(commands)
        d = distribution(delays)
        threshold = profile['thresholds']
        no_model_thread = not any(t.name == 'model-async' for t in threading.enumerate())
        passed = (
            bool(finished.value)
            and state['state'] == 'completed'
            and no_model_thread
            and len(queries) > 0
            and q['p99_ms'] <= threshold['market_query_roundtrip_p99_ms']
            and len(commands) > 0
            and c['p99_ms'] <= threshold['command_roundtrip_p99_ms']
            and d['p99_ms'] <= threshold['market_processing_delay_p99_ms']
            and shutdown <= threshold['shutdown_seconds_max']
        )
        return dict(
            case='Agent model IO/CPU parsing + complete Tools IPC',
            trial=trial,
            passed=passed,
            model_completed=bool(finished.value),
            no_app_model_thread=no_model_thread,
            market_query_roundtrip=q,
            financial_tool_roundtrip=c,
            market_processing_delay=d,
            state=state['state'],
            shutdown_seconds=shutdown,
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument(
        '--include-agent', action='store_true', help='Also measure Agent model IO and tool load.'
    )
    args = parser.parse_args()
    profile = json.loads((ROOT / 'tools/fixtures/prepared-runtime.json').read_text())
    report = dict(
        include_agent=args.include_agent,
        profile=profile,
        deployment='App dedicated execution thread',
        cases=[],
        complete=False,
        passed=False,
    )
    with tempfile.TemporaryDirectory(prefix='prepared-runtime-gate-') as directory:
        root = Path(directory)
        source = fixture(root / 'source.bin', profile)
        for case in profile['cases']:
            for trial in range(profile['repetitions']):
                result = run_case(root / 'app', source, profile, case, trial + 1)
                report['cases'].append(result)
                args.output.write_text(json.dumps(report, indent=2) + '\n')
                print(case, trial + 1, result['passed'], flush=True)
        if args.include_agent:
            report['agent_cases'] = []
            for trial in range(profile['repetitions']):
                result = agent_case(root / ('agent-' + str(trial + 1)), source, profile, trial + 1)
                report['agent_cases'].append(result)
                args.output.write_text(json.dumps(report, indent=2) + '\n')
                print('Agent model/Tools', trial + 1, result['passed'], flush=True)
    report['complete'] = True
    report['passed'] = all(case['passed'] for case in report['cases']) and all(
        case['passed'] for case in report.get('agent_cases', [])
    )
    args.output.write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__':
    main()
