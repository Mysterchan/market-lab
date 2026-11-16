"""Capture only explicitly expected child-crash diagnostics in isolated test runs."""

from functools import wraps
import os
from pathlib import Path
import subprocess
import sys


def expected_child_failure(exception_line, *, required=True, forbidden=()):
    """Run this case in a subprocess, checking its assertions and exact crash kind.

    Unexpected failures retain stdout/stderr in the assertion message. Production
    workers are untouched; the wrapper never suppresses a failing test result.
    """

    def decorate(method):
        @wraps(method)
        def run(case):
            identity = case.id()
            if os.environ.get('MARKET_LAB_FAILURE_CASE') == identity:
                return method(case)
            environment = dict(os.environ, MARKET_LAB_FAILURE_CASE=identity)
            environment['PYTHONPATH'] = os.pathsep.join(
                str(Path(path or '.').resolve()) for path in sys.path
            )
            result = subprocess.run(
                [sys.executable, '-m', 'unittest', identity, '-v'],
                env=environment,
                capture_output=True,
                text=True,
                timeout=30,
            )
            diagnostics = result.stdout + result.stderr
            case.assertEqual(result.returncode, 0, diagnostics)
            for text in forbidden:
                case.assertNotIn(text, diagnostics, 'child output included private test content')
            traces = result.stderr.count('Traceback (most recent call last):')
            case.assertIn(traces, (1,) if required else (0, 1), diagnostics)
            if traces:
                case.assertIn(exception_line + '\n', result.stderr, diagnostics)
            return None

        return run

    return decorate
