"""Compose source and execution identities; file access belongs to persistence."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Optional, Union
from sim_platform.domain.models import ContractError
from sim_platform.domain.json_values import copy_json
from sim_platform.persistence.replay.files import digest, implementation_digest, python_files

if TYPE_CHECKING:  # Annotation only.
    from os import PathLike


# Execution modules whose code determines replayed financial results.
RULE_MODULES = ('engine.py', 'liquidity.py', 'risk.py', 'models.py')


def rules_digest() -> str:
    """Digest of the execution and ledger code that decides financial results."""
    root = Path(__file__).resolve().parents[1]
    paths = [root / 'execution' / name for name in RULE_MODULES]
    # Persistence performs directory discovery as well as content reads.
    paths += python_files(root / 'domain' / 'ledger')
    return implementation_digest(root, paths)


def capture_source(descriptor: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Source descriptor plus digests of the source, native library and execution rules."""
    if descriptor is None:
        return None
    result = copy_json(descriptor)
    if result.get('kind') in ('prepared', 'prepared-merged'):
        # A prepared source is identified by its file statistics and the shape of its
        # prepared artifact; Replay prepares it again rather than hashing large files.
        result.update(rules_sha256=rules_digest())
        return result
    native = result.pop('native_path')
    result.update(
        sha256=digest(result['path']), native_sha256=digest(native), rules_sha256=rules_digest()
    )
    return result


def validate_source(
    source: Dict[str, Any], library: Optional[Union[str, PathLike[str]]] = None
) -> None:
    """Raise unless the source, native library and rules match the recorded digests.

    A prepared source (kind 'prepared') is checked by its file statistics; Replay then
    prepares it again and compares the result's shape.
    """
    if source.get('kind') in ('prepared', 'prepared-merged'):
        from sim_platform.market.prepared.builder import source_identity

        if source.get('rules_sha256') != rules_digest():
            raise ContractError('replay execution implementation mismatch')
        for item in source.get('sources', [source]):
            try:
                current = source_identity(Path(item['path']))
            except OSError as exc:
                raise ContractError('replay source file is missing: ' + item['path']) from exc
            if {k: current[k] for k in ('bytes', 'mtime_ns')} != {
                k: item['source_identity'][k] for k in ('bytes', 'mtime_ns')
            }:
                raise ContractError('replay source file changed since the run')
        return
    from sim_platform.market.native import resolve_library_path

    native = resolve_library_path(library)
    if source.get('rules_sha256') != rules_digest() or source.get('native_sha256') != digest(
        native
    ):
        raise ContractError('replay execution implementation mismatch')
    if digest(source['path']) != source['sha256']:
        raise ContractError('replay source digest mismatch')
