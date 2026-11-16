"""Supplementary 35-second idle run: exercise the real 30-second checkpoint.

Uses the same declared event rate and thresholds. This is not the short model
gate, and does not reset or relax its acceptance criteria.
"""

import argparse
import json
from pathlib import Path
import tempfile
from time import perf_counter_ns
from unittest.mock import patch
from benchmark_prepared_runtime import ROOT, fixture, run_case, distribution
from sim_platform.execution.coordinator import ExecutionCoordinator


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    profile = json.loads((ROOT / 'tools/fixtures/prepared-runtime.json').read_text())
    profile = dict(profile, case_seconds=35, repetitions=1, cases=['idle'])
    samples = []
    original = ExecutionCoordinator.checkpoint_execution

    def measured(operations, *values, **options):
        started = perf_counter_ns()
        try:
            return original(operations, *values, **options)
        finally:
            samples.append(perf_counter_ns() - started)

    with tempfile.TemporaryDirectory(prefix='prepared-checkpoint-') as temporary:
        root = Path(temporary)
        source = fixture(root / 'source.bin', profile)
        with patch.object(ExecutionCoordinator, 'checkpoint_execution', measured):
            result = run_case(root / 'app', source, profile, 'idle', 1)
    report = dict(
        profile=profile,
        complete=True,
        passed=result['passed'] and bool(samples),
        case=result,
        periodic_checkpoint=distribution(samples),
        scope='35-second idle run with no intervening financial commit; real checkpoint required',
    )
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2), flush=True)


if __name__ == '__main__':
    main()
