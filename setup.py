"""Build hook: compile the native market library into the wheel.

``pip install .`` and ``pip wheel .`` compile ``libitch_market`` (the C++ order book and
ITCH decoder loaded through ctypes) into ``sim_platform/market/_native/``, so an
installed package needs no separate ``make``. The wheel is then tagged for the platform
(``py3-none-<platform>``): the library does not use the Python C API, so one wheel per
platform serves every supported Python version.

If no C++17 compiler is available the build continues without the library and the
wheel stays pure Python (a source checkout can still use ``make all``). Set
``MARKET_LAB_REQUIRE_NATIVE=1`` to make a failed compile an error, as release builds do.
Project metadata lives in ``pyproject.toml``; the compile step is ``tools/native_build.py``.
"""

import os
from pathlib import Path
import platform as host
import re
import sys

from setuptools import setup
from setuptools.command.build_py import build_py

try:
    from setuptools.command.bdist_wheel import bdist_wheel
except ImportError:  # setuptools older than 70.1 provides it through the wheel package.
    from wheel.bdist_wheel import bdist_wheel

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'tools'))
from native_build import LIBRARY, compile_library  # noqa: E402


class BuildWithNative(build_py):
    """build_py that also places the compiled native library in the package."""

    def run(self) -> None:
        super().run()
        target = Path(self.build_lib) / 'sim_platform' / 'market' / '_native' / LIBRARY
        try:
            compile_library(ROOT, target)
        except Exception as exc:  # A missing compiler leaves a pure-Python wheel.
            if os.environ.get('MARKET_LAB_REQUIRE_NATIVE') == '1':
                raise
            print(f'warning: native market library not built ({exc}); use make all', flush=True)
            return
        self.distribution.native_built = True


class PlatformWheel(bdist_wheel):
    """Tag the wheel ``py3-none-<platform>`` when it carries the native library.

    On macOS the platform tag names the architecture the library was compiled for
    (``ARCHFLAGS`` or this machine's), not the interpreter's universal build.
    """

    def get_tag(self):
        if not getattr(self.distribution, 'native_built', False):
            return super().get_tag()
        self.root_is_pure = False
        tag = super().get_tag()[2]
        if sys.platform == 'darwin':
            flags = os.environ.get('ARCHFLAGS', '')
            arches = re.findall(r'-arch\s+(\S+)', flags) or [host.machine()]
            arch = 'universal2' if set(arches) == {'arm64', 'x86_64'} else arches[0]
            tag = re.sub(r'(universal2|x86_64|arm64|intel)$', arch, tag)
        return ('py3', 'none', tag)


setup(cmdclass=dict(build_py=BuildWithNative, bdist_wheel=PlatformWheel))
