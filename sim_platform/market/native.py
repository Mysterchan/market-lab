"""ctypes adapter to the original C++ decoder/book; no Python order book."""

from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import sys
from typing import Any, Dict, Optional, Union

PathInput = Union[str, 'os.PathLike[str]']


LIBRARY_ENVIRONMENT = 'MARKET_LAB_NATIVE_LIBRARY'


def resolve_library_path(library: Optional[PathInput] = None) -> Path:
    """Resolve the native adapter library without opening it or creating a book.

    The first of these wins: the explicit ``library`` argument, the
    ``MARKET_LAB_NATIVE_LIBRARY`` environment variable, a library shipped inside the
    package (``sim_platform/market/_native/``), then the source checkout's ``build/``.
    The path is returned even when the file does not exist; opening it reports that.
    """
    if library:
        return Path(library).resolve()
    configured = os.environ.get(LIBRARY_ENVIRONMENT)
    if configured:
        return Path(configured).expanduser().resolve()
    suffix = (
        '.dylib' if sys.platform == 'darwin' else ('.dll' if sys.platform == 'win32' else '.so')
    )
    name = 'libitch_market' + suffix
    packaged = Path(__file__).resolve().parent / '_native' / name
    if packaged.is_file():
        return packaged
    return (Path(__file__).resolve().parents[2] / 'build' / name).resolve()


class NativeEngine:
    """One native decoder and order book for one symbol.

    Every method that returns a mapping returns the engine's JSON result for that
    call. The engine holds a native handle; call ``close()`` when finished.

    Args:
        symbol: The stock to rebuild; other symbols in the source are ignored.
        library: Path to the compiled adapter; by default the one under ``build/``.
        depth: Number of price levels per side to return in each snapshot.
    """

    def __init__(self, symbol: str, library: Optional[PathInput] = None, depth: int = 5) -> None:
        self.library_path = resolve_library_path(library)
        self.lib = ctypes.CDLL(str(self.library_path))
        self.lib.market_create.argtypes = [ctypes.c_char_p]
        self.lib.market_create.restype = ctypes.c_void_p
        self.lib.market_destroy.argtypes = [ctypes.c_void_p]
        self.lib.market_destroy.restype = None
        self.lib.market_error.argtypes = [ctypes.c_void_p]
        self.lib.market_error.restype = ctypes.c_char_p
        self.lib.market_apply.argtypes = [
            ctypes.c_void_p,
            ctypes.c_char_p,
            ctypes.c_size_t,
            ctypes.c_uint64,
            ctypes.c_size_t,
        ]
        self.lib.market_apply.restype = ctypes.c_char_p
        self.lib.market_open_file.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
        self.lib.market_open_file.restype = ctypes.c_int
        self.lib.market_peek_file.argtypes = [ctypes.c_void_p]
        self.lib.market_peek_file.restype = ctypes.c_char_p
        self.lib.market_next_file.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
        self.lib.market_next_file.restype = ctypes.c_char_p
        self.lib.market_inspect.argtypes = [
            ctypes.c_void_p,
            ctypes.c_char_p,
            ctypes.c_size_t,
            ctypes.c_uint64,
        ]
        self.lib.market_inspect.restype = ctypes.c_char_p
        self.handle = self.lib.market_create(symbol.encode('ascii'))
        if not self.handle:
            raise RuntimeError('cannot create native market engine')
        self.depth = depth

    def apply(self, payload: bytes, sequence: int) -> Dict[str, Any]:
        """Apply one message to the book and return the resulting event."""
        self._check_open()
        return self._decode(
            self.lib.market_apply(self.handle, payload, len(payload), sequence, self.depth)
        )

    def _check_open(self) -> None:
        if not self.handle:
            raise RuntimeError('engine is closed')

    def _decode(self, raw: Optional[bytes]) -> Any:
        """Parse a native JSON result; a null result carries the engine's error text."""
        if raw is None:
            raise ValueError(self.lib.market_error(self.handle).decode())
        return json.loads(raw)

    def open_file(self, path: PathInput, allow_implicit_eof: bool = False) -> None:
        self._check_open()
        if not self.lib.market_open_file(self.handle, os.fsencode(path), allow_implicit_eof):
            raise ValueError(self.lib.market_error(self.handle).decode())

    def peek_file(self) -> Any:
        """Return the sequence and time of the next file record without applying it."""
        self._check_open()
        return self._decode(self.lib.market_peek_file(self.handle))

    def next_file(self) -> Dict[str, Any]:
        self._check_open()
        return self._decode(self.lib.market_next_file(self.handle, self.depth))

    def inspect(self, payload: bytes, sequence: int) -> Dict[str, Any]:
        """Decode one message without changing the book."""
        self._check_open()
        return self._decode(self.lib.market_inspect(self.handle, payload, len(payload), sequence))

    def close(self) -> None:
        if getattr(self, 'handle', None):
            self.lib.market_destroy(self.handle)
            self.handle = None

    def __del__(self) -> None:
        self.close()
