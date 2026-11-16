"""Correctness checks for the benchmark, not speed assertions."""

import argparse
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tools.benchmark_platform import (
    Timings,
    aggregate,
    core_trial,
    prepare_fixture,
    save,
    synthetic_fixture,
)


class PlatformBenchmarkTests(unittest.TestCase):
    def test_native_validated_prefix_preserves_source_and_cutoff(self):
        with TemporaryDirectory(prefix='benchmark-test-') as temporary:
            folder = Path(temporary)
            source, prefix = folder / 'source.bin', folder / 'prefix.bin'
            original = synthetic_fixture(source, 12, 3)
            before = source.read_bytes()
            measured = prepare_fixture(source, prefix, 1, 'AAPL')
            self.assertEqual(source.read_bytes(), before)
            self.assertEqual(measured['origin'], original['origin'])
            self.assertEqual(measured['playback_frames'], 4)
            self.assertEqual(measured['end_time_ns'], original['origin']['time_ns'] + 1_000_000_000)
            self.assertTrue(prefix.read_bytes().endswith(b'\0\0'))

    def test_instrumentation_does_not_change_rule_results_or_leak_patches(self):
        from sim_platform.persistence.sql.database import Database

        original = Database.unit_of_work
        with TemporaryDirectory(prefix='benchmark-test-') as temporary:
            folder = Path(temporary)
            source = folder / 'source.bin'
            meta = folder / 'fixture.json'
            save(meta, synthetic_fixture(source, 8, 3))
            results = []
            for profile in (False, True):
                args = argparse.Namespace(
                    worker='rule_trade',
                    input=source,
                    fixture_meta=meta,
                    symbol='AAPL',
                    profile=profile,
                )
                timings = Timings()
                try:
                    if profile:
                        timings.install()
                    result = core_trial(args, folder / ('profile' if profile else 'plain'), timings)
                    results.append(result)
                    if profile:
                        self.assertGreater(timings.result()['database.write_uow']['calls'], 0)
                finally:
                    timings.close()
            self.assertEqual(results[0]['frames'], 8)
            self.assertEqual(results[0]['status'], 'completed')
            self.assertEqual(results[0]['final'], results[1]['final'])
            self.assertIs(Database.unit_of_work, original)

    def test_aggregate_excludes_profile_and_failed_trials(self):
        sample = dict(
            case='native',
            instrumented=False,
            status='completed',
            wall_seconds=2,
            preparation_seconds=1,
            speed_x=3,
            frames_per_second=10,
            peak_rss_bytes=20,
            database_bytes_after_close=0,
            cleanup_seconds=0.1,
            frames=20,
            simulated_seconds=6,
            final={'sha256': 'same'},
        )
        samples = [
            sample,
            {**sample, 'wall_seconds': 4},
            {**sample, 'instrumented': True, 'wall_seconds': 100},
            dict(case='native', instrumented=False, status='failed'),
        ]
        summary = aggregate(samples)[0]
        self.assertEqual(summary['wall_seconds'], 3)
        self.assertEqual(summary['completed'], 2)
        self.assertEqual(summary['failed'], 1)
        self.assertTrue(summary['deterministic'])


if __name__ == '__main__':
    unittest.main()
