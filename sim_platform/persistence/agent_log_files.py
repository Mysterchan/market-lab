"""Ordinary JSON objects, one per line; append during a run, read once at end.

No SQL, digest, per-call fsync or complete-file rewrite. The caller chooses an
absolute file path outside the auto-cleaned workspace. Failed/unfinished files
remain available and cannot be archived as successful completed logs.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, NoReturn, Tuple, Union

from sim_platform.domain.json_values import encode
from sim_platform.domain.models import ContractError

if TYPE_CHECKING:  # Annotation only.
    from os import PathLike


# A parsed log: 'header', 'records' (list) and 'end'.
AgentLogArchive = Dict[str, Any]


def default_agent_log_path(directory: Union[str, PathLike[str]], run_id: str) -> Path:
    """Platform-selected persistent log location; create only this artifact folder."""
    folder = Path(directory) / 'agent-logs'
    folder.mkdir(parents=True, exist_ok=True)
    return folder / (run_id + '.jsonl')


class AgentLogFile:
    """An append-only JSON-lines activity log for one run.

    Created by experiment assembly (the file must not exist yet) and wrapped by the
    ExperimentLog, which closes it. A failed write closes the stream.
    """

    def __init__(self, path: Union[str, PathLike[str]]) -> None:
        self.path = _absolute_path(path)
        self._stream = self.path.open('x', encoding='utf-8', newline='')

    def append(self, value: Any) -> None:
        if self._stream.closed:
            raise ContractError('agent log file is closed')
        text = encode(value) + '\n'
        try:
            self._stream.write(text)
            self._stream.flush()
        except Exception:
            self._stream.close()
            raise

    def close(self) -> None:
        self._stream.close()


def read_agent_log(path: Union[str, PathLike[str]]) -> AgentLogArchive:
    """Parse a sealed log outside the SQL UOW; incomplete or malformed files fail."""
    archive: AgentLogArchive = {'header': None, 'records': [], 'end': None}
    with _absolute_path(path).open(encoding='utf-8') as stream:
        for line in stream:
            if not line.endswith('\n') or archive['end'] is not None:
                raise ContractError('agent log is truncated or has trailing records')
            try:
                item = json.loads(
                    line, object_pairs_hook=_unique_object, parse_constant=_invalid_constant
                )
            except (ValueError, RecursionError) as exc:
                raise ContractError('invalid agent log JSON') from exc
            _collect(archive, item)
    if archive['header'] is None or archive['end'] is None:
        raise ContractError('agent log is unfinished')
    return archive


def _absolute_path(path: Union[str, PathLike[str]]) -> Path:
    path = Path(path).expanduser()
    if not path.is_absolute():
        raise ContractError('agent log path must be absolute')
    return path


def _collect(archive: AgentLogArchive, item: Any) -> None:
    if type(item) is not dict or len(item) != 1:
        raise ContractError('invalid agent log line')
    if archive['header'] is None:
        if set(item) != {'header'}:
            raise ContractError('agent log must start with its header')
        archive['header'] = item['header']
    elif set(item) == {'record'}:
        archive['records'].append(item['record'])
    elif set(item) == {'end'}:
        archive['end'] = item['end']
    else:
        raise ContractError('invalid agent log line order')


def _unique_object(pairs: List[Tuple[str, Any]]) -> Dict[str, Any]:
    value: Dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('duplicate JSON key')
        value[key] = item
    return value


def _invalid_constant(value: str) -> NoReturn:
    raise ValueError('non-finite JSON value')
