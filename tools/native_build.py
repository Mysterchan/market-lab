"""Compile the native market library with the platform's own compiler, without Make.

Used by ``setup.py`` (wheels) and ``tools/build_desktop.py`` (installers, including
Windows, which has no Make). Produces ``libitch_market.dylib``, ``.so`` or ``.dll``:
the C++ order book and ITCH decoder that ``sim_platform.market.native`` loads.

    python tools/native_build.py build/libitch_market.dylib
"""

import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile

SOURCES = (
    'src/bridge/market.cpp',
    'src/bridge/engine.cpp',
    'src/bridge/prepared.cpp',
    'src/feed/itch/decoder.cpp',
    'src/book/book.cpp',
    'src/common/binary.cpp',
    'src/feed/binaryfile/reader.cpp',
)
SUFFIX = '.dylib' if sys.platform == 'darwin' else '.dll' if sys.platform == 'win32' else '.so'
LIBRARY = 'libitch_market' + SUFFIX


def _compiler():
    try:
        from setuptools._distutils.ccompiler import new_compiler
        from setuptools._distutils.sysconfig import customize_compiler
    except ImportError:  # Python 3.9 without a recent setuptools still has distutils.
        from distutils.ccompiler import new_compiler
        from distutils.sysconfig import customize_compiler
    compiler = new_compiler()
    customize_compiler(compiler)
    return compiler


def _compile_msvc(root: Path, target: Path) -> None:
    compiler = _compiler()
    with tempfile.TemporaryDirectory() as work:
        objects = compiler.compile(
            [str(root / name) for name in SOURCES],
            output_dir=work,
            include_dirs=[str(root / 'include')],
            extra_preargs=['/std:c++17', '/EHsc', '/O2'],
        )
        built = Path(work) / LIBRARY
        # MSVC exports nothing by default; the bridge's extern "C" functions are listed.
        compiler.link_shared_object(
            objects, str(built), target_lang='c++', export_symbols=exported_symbols(root)
        )
        shutil.copyfile(built, target)


def compile_library(root: Path, target: Path) -> Path:
    """Compile and link the market library from ``root``'s sources to ``target``.

    Unix uses ``$CXX`` (default clang++ on macOS, g++ elsewhere) with the Makefile's
    flags; ``ARCHFLAGS`` (for example ``-arch arm64 -arch x86_64``) selects macOS
    architectures. Windows uses the MSVC compiler that Python was built with.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    if sys.platform == 'win32':
        _compile_msvc(root, target)
        return target
    cxx = os.environ.get('CXX') or ('clang++' if sys.platform == 'darwin' else 'g++')
    command = [cxx, '-std=c++17', '-O2', '-fPIC', '-I' + str(root / 'include')]
    if sys.platform == 'darwin':
        command += ['-dynamiclib', '-install_name', '@loader_path/' + LIBRARY]
        command += shlex.split(os.environ.get('ARCHFLAGS', ''))
    else:
        command += ['-shared']
    command += [str(root / name) for name in SOURCES] + ['-o', str(target)]
    subprocess.run(command, check=True)
    return target


def exported_symbols(root: Path) -> list:
    """Names of the ``market_*`` / ``prepared_*`` C functions the bridge defines."""
    import re

    names = []
    for name in ('src/bridge/market.cpp', 'src/bridge/prepared.cpp'):
        text = (root / name).read_text(encoding='utf-8')
        names += re.findall(r'^\w[\w\s\*]*?\b((?:market|prepared)_\w+)\s*\(', text, re.M)
    return sorted(set(names))


if __name__ == '__main__':
    here = Path(__file__).resolve().parents[1]
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else here / 'build' / LIBRARY
    print(compile_library(here, output))
