"""Bounded native export batches; no per-frame Python objects or JSON."""

from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from sim_platform.domain.models import ContractError
from sim_platform.market.native import PathInput, resolve_library_path


class NativePreparation:
    """Own an exporter handle; caller owns staging paths and closes this adapter."""

    def __init__(
        self,
        source: PathInput,
        stage: Path,
        symbol: str,
        depth: int,
        library: Optional[PathInput],
        implicit: bool,
        profile: bool,
    ) -> None:
        self._handle: Any = None
        self._lib = lib = ctypes.CDLL(str(resolve_library_path(library)))
        try:
            lib.prepared_create.argtypes = [ctypes.c_char_p] * 4 + [
                ctypes.c_size_t,
                ctypes.c_int,
                ctypes.c_int,
            ]
            lib.prepared_create.restype = ctypes.c_void_p
            lib.prepared_next.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
            lib.prepared_next.restype = ctypes.c_char_p
            lib.prepared_error.argtypes = [ctypes.c_void_p]
            lib.prepared_error.restype = ctypes.c_char_p
            lib.prepared_destroy.argtypes = [ctypes.c_void_p]
            lib.prepared_destroy.restype = None
            self._handle = lib.prepared_create(
                os.fsencode(source),
                symbol.encode('ascii'),
                os.fsencode(stage / 'events.bin'),
                os.fsencode(stage / 'states.bin'),
                depth,
                implicit,
                profile,
            )
            if not self._handle:
                raise ContractError(
                    'cannot create native preparation output; check source and staging paths'
                )
        except Exception:
            self.close()
            raise

    def next_batch(self, frames: int = 8192) -> Dict[str, Any]:
        """Export up to ``frames`` more source frames and return the batch summary."""
        if not self._handle:
            raise ContractError('native preparation closed')
        raw = self._lib.prepared_next(self._handle, frames)
        if raw is None:
            raise ContractError(self._lib.prepared_error(self._handle).decode())
        return json.loads(raw)

    def close(self) -> None:
        if self._handle:
            self._lib.prepared_destroy(self._handle)
            self._handle = None
