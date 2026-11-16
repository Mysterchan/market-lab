"""Inspect a trusted interpreter without importing user site packages or project code."""

from __future__ import annotations

import json
import platform
import sys
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Union
from sim_platform.agents.preparation.process import run_command
from sim_platform.agents.preparation.errors import PreparationError

PROBE = '''import json, sys, platform
print(json.dumps(dict(version=platform.python_version(), implementation=sys.implementation.name,
    executable=sys.executable, base_prefix=sys.base_prefix, platform=sys.platform,
    machine=platform.machine(), cache_tag=sys.implementation.cache_tag)))
'''


# Seconds allowed for the probe command.
PROBE_TIMEOUT_SECONDS = 15


def inspect_python(executable: Union[str, Path], cancelled: Callable[[], bool]) -> Dict[str, Any]:
    """Run the probe in ``executable``; require native CPython for this OS and CPU."""
    value = json.loads(
        run_command(
            [str(executable), '-I', '-S', '-c', PROBE],
            cancelled,
            timeout=PROBE_TIMEOUT_SECONDS,
            error_code='INVALID_PYTHON_RUNTIME',
        )
    )

    def machine(name: Optional[str]) -> Optional[str]:
        return 'arm64' if name in ('aarch64', 'arm64') else name

    if (
        value.get('implementation') != 'cpython'
        or value.get('platform') != sys.platform
        or machine(value.get('machine')) != machine(platform.machine())
        or not Path(value.get('executable', '')).is_file()
    ):
        raise PreparationError(
            'INVALID_PYTHON_RUNTIME', 'runtime must be native CPython for this OS and CPU'
        )
    return value


def app_python() -> Optional[Dict[str, Any]]:
    """Describe the App's interpreter for explicit compatible repo use; frozen Apps cannot lend it."""
    if getattr(sys, 'frozen', False):
        return None
    return dict(
        version=platform.python_version(),
        implementation=sys.implementation.name,
        executable=sys.executable,
        base_prefix=sys.base_prefix,
        platform=sys.platform,
        machine=platform.machine(),
        cache_tag=sys.implementation.cache_tag,
        runtime_ref='app',
        origin='app',
        state='ready',
    )
