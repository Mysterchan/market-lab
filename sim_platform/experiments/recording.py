"""Ordered ordinary JSON experiment stream, written only by output stage."""

from __future__ import annotations

from typing import Any, Dict

from sim_platform.domain.json_values import copy_json
from sim_platform.domain.models import ContractError


class ExperimentLog:
    """Numbered JSON records of one run, written in order by the output thread.

    Assembly creates it with a log-file adapter (AgentLogFile from the persistence
    package, not yet typed) and writes the header at once. The 'end' message writes
    the trailer; close() closes the adapter.

    Args:
        adapter: Append-only JSON writer with ``path``, ``append(value)`` and ``close()``.
        run_id: Run recorded in the header.
    """

    def __init__(self, adapter: Any, run_id: str) -> None:
        self.adapter, self.path = adapter, adapter.path
        self.number, self.ended = 0, False
        adapter.append(dict(header=dict(run_id=run_id)))

    def __call__(self, message: Dict[str, Any]) -> None:
        if self.ended:
            raise ContractError('experiment log already ended')
        self.number += 1
        self.adapter.append(dict(record=dict(index=self.number, **copy_json(message))))
        if message['kind'] == 'end':
            self.adapter.append(dict(end=dict(records=self.number, status=message['status'])))
            self.ended = True

    def close(self) -> None:
        self.adapter.close()
