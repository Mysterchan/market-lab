"""Pacing uses source time and never advances future data early."""

from pathlib import Path
from contextlib import closing
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from sim_platform import Platform, ContractError
from tools.benchmark_platform import synthetic_fixture


class PacingTests(unittest.TestCase):
    def test_one_times_and_resume_anchor(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'source.bin'
            synthetic_fixture(source, 100, 1)
            with Platform(root / 'db') as app:
                with closing(app.open_research()) as controller:
                    controller.load(source)
                    for _ in range(3):
                        controller.step()
                    origin = controller.market.time_ns
                    with patch('sim_platform.market.pacing.monotonic', return_value=100):
                        controller.play(speed=1)
                        controller.tick(render=False, max_events=1000)
                        self.assertEqual(controller.market.time_ns, origin)
                    with patch('sim_platform.market.pacing.monotonic', return_value=100.25):
                        controller.tick(render=False, max_events=1000)
                        self.assertLessEqual(controller.market.time_ns, origin + 250_000_000)
                        self.assertGreater(
                            controller.market.next_event_time_ns, origin + 250_000_000
                        )
                    controller.pause()
                    paused = controller.market.time_ns
                    with patch('sim_platform.market.pacing.monotonic', return_value=200):
                        controller.play(speed=2)
                        controller.tick(render=False, max_events=1000)
                        self.assertEqual(controller.market.time_ns, paused)
                    with patch('sim_platform.market.pacing.monotonic', return_value=200.1):
                        controller.tick(render=False, max_events=1000)
                        self.assertLessEqual(controller.market.time_ns, paused + 200_000_000)
                    controller.pause()
                    for invalid in (0, -1, True, float('nan'), float('inf')):
                        with self.assertRaises(ContractError):
                            controller.play(speed=invalid)


if __name__ == '__main__':
    unittest.main()
