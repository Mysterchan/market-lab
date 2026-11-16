"""Owner-facing JSON recording; no SQL, execution callbacks, clocks or hashing.

The injected file adapter owns IO. Record only completed tool calls and useful
control boundaries, then seal the file after the run ends. SQL archiving is a
separate Platform operation, never part of a tool call.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, Optional

from sim_platform.domain.json_values import copy_json
from sim_platform.domain.models import ContractError
from sim_platform.domain.operation_position import OperationPosition

TERMINAL_STATES = ('completed', 'stopped', 'failed')
# Most digits in a position counter.
COUNTER_MAX_DIGITS = 20


def _require_fields(value: Any, fields: Iterable[str], message: str) -> None:
    """Reject missing/extra fields before callers access the object's members."""
    if type(value) is not dict or set(value) != fields:
        raise ContractError(message)


def _validate_counter(value: Any) -> None:
    """Allow 0 or positive ASCII digits, with at most 20 digits and no leading zero."""
    message = 'agent position counters must be decimal strings'
    if not isinstance(value, str):
        raise ContractError(message)
    if not value.isascii() or not value.isdecimal():
        raise ContractError(message)
    if len(value) > COUNTER_MAX_DIGITS:
        raise ContractError(message)
    if len(value) > 1 and value.startswith('0'):
        raise ContractError(message)


def validate_position(position: Any) -> None:
    """Require four canonical decimal counters; do not interpret clock semantics."""
    OperationPosition.from_wire(position)


def _validate_header(header: Any) -> None:
    references = ('profile_id', 'experiment_id', 'run_id', 'source_ref')
    fields = {'profile_id', 'experiment_id', 'run_id', 'source_ref'}
    _require_fields(header, fields, 'unsupported agent operation log header')
    for name in references:
        if not isinstance(header[name], str) or not header[name]:
            raise ContractError('invalid agent log reference')


def _validate_tool_record(record: Dict[str, Any]) -> None:
    fields = {'index', 'kind', 'position', 'request', 'result', 'receipt_target_ns'}
    _require_fields(record, fields, 'invalid recorded tool call')
    if type(record['request']) is not dict:
        raise ContractError('invalid recorded tool call')
    if type(record['result']) is not dict:
        raise ContractError('invalid recorded tool call')

    target = record['receipt_target_ns']
    if target is not None:
        _validate_counter(target)


def _validate_control_record(record: Dict[str, Any]) -> None:
    fields = {'index', 'kind', 'position', 'event', 'details'}
    _require_fields(record, fields, 'invalid recorded control')
    if not isinstance(record['event'], str) or not record['event']:
        raise ContractError('invalid control description')
    if type(record['details']) is not dict:
        raise ContractError('invalid control description')


def _validate_record(record: Any, expected_index: int) -> None:
    """Check sequence first, then the kind-specific payload, then its position."""
    if type(record) is not dict:
        raise ContractError('agent operation sequence has a gap')
    if type(record.get('index')) is not int or record['index'] != expected_index:
        raise ContractError('agent operation sequence has a gap')

    kind = record.get('kind')
    if kind == 'tool':
        _validate_tool_record(record)
    elif kind == 'control':
        _validate_control_record(record)
    else:
        raise ContractError('unsupported agent operation kind')
    validate_position(record['position'])


def _validate_records(records: Any) -> None:
    if type(records) is not list:
        raise ContractError('agent records must be a list')
    for expected_index, record in enumerate(records, start=1):
        _validate_record(record, expected_index)


def _validate_end(end: Any, record_count: int) -> None:
    message = 'agent log is incomplete or has an invalid ending'
    _require_fields(end, {'status', 'record_count'}, message)
    if end['status'] not in TERMINAL_STATES:
        raise ContractError(message)
    if type(end['record_count']) is not int:
        raise ContractError(message)
    if end['record_count'] != record_count:
        raise ContractError(message)


def validate_archive(value: Any) -> Dict[str, Any]:
    """Return detached JSON after checking header, record sequence and ending.

    This runs before the SQL transaction. Ownership and run state are checked
    separately by AgentLogService; this function only checks the file format.
    """
    archive = copy_json(value)
    _require_fields(archive, {'header', 'records', 'end'}, 'invalid agent operation archive')
    _validate_header(archive['header'])
    _validate_records(archive['records'])
    _validate_end(archive['end'], len(archive['records']))
    return archive


class AgentOperationLog:
    """Single-owner recorder; closing an unfinished file does not mark it complete.

    Assembly creates one per Agent run around an append-only JSON-lines file
    (persistence AgentLogFile, which agents may not import); the output thread
    records into it and finish() seals and closes it.
    """

    def __init__(self, file_adapter: Any, header: Dict[str, Any]) -> None:
        self._file = file_adapter
        self.header = copy_json(header)
        self._count = 0
        self._finished = False
        self._on_close: Callable[[], None] = lambda: None
        self._file.append({'header': self.header})

    @property
    def path(self) -> Any:
        return self._file.path

    def on_close(self, callback: Callable[[], None]) -> None:
        self._on_close = callback

    def close(self) -> None:
        """Release file and root registration; unfinished logs remain unfinished."""
        self._file.close()
        self._on_close()

    def _append(self, value: Dict[str, Any]) -> int:
        if self._finished:
            raise ContractError('agent log is already finished')
        record = copy_json(dict(value, index=self._count + 1))
        _validate_record(record, self._count + 1)
        self._file.append({'record': record})
        self._count += 1
        return self._count

    def record_tool(
        self,
        request: Dict[str, Any],
        result: Any,
        position: Dict[str, Any],
        *,
        receipt_target_ns: Optional[str] = None,
    ) -> int:
        """Record an existing tool result; never execute or persist the trade here."""
        return self._append(
            dict(
                kind='tool',
                request=request,
                result=result,
                position=position,
                receipt_target_ns=receipt_target_ns,
            )
        )

    def record_control(
        self, event: str, position: Dict[str, Any], details: Optional[Dict[str, Any]] = None
    ) -> int:
        """Record a boundary after the owner applies it; this does not apply controls."""
        return self._append(
            dict(
                kind='control',
                event=event,
                position=position,
                details={} if details is None else details,
            )
        )

    def finish(self, status: str) -> None:
        """Seal after completed/stopped/failed; archive separately, outside the loop."""
        if self._finished or status not in TERMINAL_STATES:
            raise ContractError('invalid or repeated agent log finish')
        self._file.append({'end': dict(status=status, record_count=self._count)})
        self._finished = True
        self.close()
