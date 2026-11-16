"""Trusted bridge: owner publishes versions; Live Runtime owns policy.

No scheduler here. All waiting is off-owner (ingress/control or output). Event
publication is bounded and fails closed rather than losing financial versions.
"""

from __future__ import annotations
import logging

from concurrent.futures import Future
from threading import RLock
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Mapping, Optional, Tuple, cast

from sim_platform.domain.json_values import copy_json
from sim_platform.domain.models import ContractError
from sim_platform.experiments.scheduling.journal import EventJournal
from sim_platform.experiments.execution.results import DeferredResult
from sim_platform.agents.tools.protocol.contracts import ToolError
from sim_platform.agents.tools.protocol.responses import (
    success_response,
    cutoff,
    response_size,
    error_response,
)

_log = logging.getLogger(__name__)

if TYPE_CHECKING:  # Annotation only.
    from sim_platform.experiments.execution.session import ExperimentSession
    from sim_platform.experiments.scheduling.host import ExperimentRuntimeHandle

# Most events in one published notification batch.
NOTIFICATION_BATCH = 16
# Longest wait ID and due-time text used to bound a control tool's response size.
WAIT_ID_BUDGET = 128
DUE_TEXT_BUDGET = 20

# Captures detached values for a tool name ('account.snapshot', 'orders.list', ...).
Capture = Callable[[str], Mapping[str, Any]]


class RuntimeBinding:
    """Connects the financial owner to the Runtime child for one Agent or Rule run.

    Assembly creates it and binds it to the pipeline as its lifecycle; it publishes
    market/order notifications to the Runtime, forwards work/tool controls and
    records Runtime failures, which end the run at the owner's next step.

    Args:
        runtime: The Runtime process handle.
        pipeline: The run's ExperimentSession.
        binding: Run binding: 'run_id', 'symbol' and, for Agents, the tool configuration.
        capture: Captures detached account/market values on the owner.
    """

    # Set in __init__; declared here because the callbacks are defined first.
    runtime: ExperimentRuntimeHandle
    pipeline: ExperimentSession
    binding: Dict[str, Any]
    capture: Capture
    _lock: RLock
    _error: Optional[Exception]
    _journal: EventJournal
    _resyncing: bool

    def failed(self, error: Exception) -> None:
        with self._lock:
            self._error = error
        self.pipeline.wake()

    def _resynchronized(self, future: Optional[Future[Any]]) -> None:
        with self._lock:
            self._resyncing = False
            sequence = self._journal.sequence
        if future is not None and future.exception() is not None:
            self.failed(cast(Exception, future.exception()))
        elif sequence > self.runtime.snapshot().get('event_sequence', 0):
            self._recover_notifications()

    def _submit(self, command: str, data: Optional[Dict[str, Any]] = None) -> Future[Any]:
        result = self.runtime.submit(command, data)
        result.add_done_callback(self._checked)
        return result

    def resynchronize(self, sequence: int) -> List[Future[Any]]:
        """Trusted adapter replays retained canonical batches; never skip a lost fill."""
        with self._lock:
            entries = self._journal.after(sequence)
        self.record_notice(
            'notification_resync', dict(after_sequence=sequence, retained_batches=len(entries))
        )
        return [self._submit('observe', entry) for entry in entries]

    def _recover_notifications(self) -> None:
        """Replay a retained prefix off-owner; a lost prefix is a platform fault."""
        with self._lock:
            if self._resyncing:
                return
            self._resyncing = True
        try:
            pending = self.resynchronize(self.runtime.snapshot().get('event_sequence', 0))
            if pending:
                pending[-1].add_done_callback(self._resynchronized)
            else:
                self._resynchronized(None)
        except Exception as exc:
            _log.warning('Handled failure in _recover_notifications', exc_info=True)
            self.failed(exc)

    def _checked(self, future: Future[Any]) -> None:
        try:
            future.result()
        except Exception as exc:
            _log.warning('Handled failure in _checked', exc_info=True)
            if getattr(exc, 'code', None) == 'NOTIFICATION_GAP':
                self._recover_notifications()
            else:
                self.failed(exc)

    def record_notice(self, event: str, details: Dict[str, Any]) -> None:
        self.pipeline.publish_notice(event, details)
        if event == 'budget_exceeded':
            # The run's model budget is spent: end it cleanly, recorded as 'budget'.
            self.pipeline.sequencer.stop('budget')
            self.pipeline.wake()

    def __init__(
        self,
        runtime: ExperimentRuntimeHandle,
        pipeline: ExperimentSession,
        binding: Dict[str, Any],
        capture: Capture,
    ) -> None:
        self.runtime, self.pipeline = runtime, pipeline
        self.binding, self.capture = binding, capture
        self._lock = RLock()
        self._error = None
        self._sequence: Optional[int] = None
        self._account_sequence: Optional[int] = None
        self._source_time: Optional[int] = None
        self._orders: Dict[str, int] = {}
        self._notices: List[Tuple[str, Dict[str, Any]]] = [
            ('runtime_ready', dict(run_id=binding.get('run_id'), support_scope='trusted-local'))
        ]
        self._journal = EventJournal()
        self._event_sequence = 0
        self._resyncing = False
        self.notice: Callable[[str, Dict[str, Any]], None] = lambda event, details: None
        self.on_end: Callable[[], None] = lambda: None
        self.runtime.bind_callbacks(on_failure=self.failed, on_notice=self.record_notice)

    @property
    def published_sequence(self) -> int:
        """How many market notification batches have been published to the Runtime."""
        with self._lock:
            return int(self._journal.sequence)

    def snapshot(self) -> Dict[str, Any]:
        """Current work fields with the 'attempt' and full 'runtime' views."""
        view = self.runtime.snapshot()
        work = view.get('work', {})
        return dict(copy_json(work), attempt=view.get('attempt', {}), runtime=view)

    def validate_identity(self, request: Dict[str, Any]) -> None:
        view = self.snapshot()
        if (
            request['work_id'] != view.get('work_id')
            or request['epoch'] != view.get('epoch')
            or view.get('state') != 'running'
        ):
            raise ToolError('STALE_WORK', 'work is no longer active')

    def admit_request(self, request: Dict[str, Any]) -> Future[Any]:
        return self.runtime.submit(
            'admit', {k: request[k] for k in ('work_id', 'epoch', 'request_id')}
        )

    def delivered(self, request_id: str) -> None:
        if not self.runtime.snapshot()['closed']:
            try:
                self._submit('deliver', dict(request_id=request_id))
            except ContractError:
                if not self.pipeline.channels.cancel.is_set():
                    raise

    def agent_failed(self, reason: str) -> None:
        self._submit('agent_failed', dict(reason=reason))

    def observe(self, position: Dict[str, str]) -> None:
        """Publish market/order changes since the last observation to the Runtime."""
        captured = self.capture('account.snapshot')
        account = captured['execution'].account
        source_time = captured['market'].time_ns
        if (
            self._source_time == source_time
            and self._sequence == int(position['sequence'])
            and self._account_sequence == account.sequence
        ):
            return
        sequence = int(position['sequence'])
        values = self.capture('orders.list')
        orders = {
            o.order_id: values['order_versions'][o.order_id]
            for o in account.orders
            if o.run_id == self.binding['run_id']
        }
        events: List[Dict[str, Any]] = []
        if (
            self._sequence is not None
            and (sequence != self._sequence or source_time != self._source_time)
            and captured.get('trigger_enabled', True)
        ):
            events.append(dict(kind='market_update', symbol=self.binding['symbol']))
        for identity, version in orders.items():
            if version != self._orders.get(identity):
                events.append(dict(kind='order_update', order_id=identity))
        # Every accepted batch has a contiguous id; baseline includes all order
        # versions, so wait registered before/after this publication is equivalent.
        for offset in range(0, max(1, len(events)), NOTIFICATION_BATCH):
            with self._lock:
                entry = self._journal.append(
                    dict(sequence=sequence, orders=orders),
                    events[offset : offset + NOTIFICATION_BATCH],
                )
            self._event_sequence = entry['event_sequence']
            self.notice('market_notification', entry)
            self._submit('observe', entry)
        self._sequence, self._account_sequence, self._orders = sequence, account.sequence, orders
        self._source_time = source_time

    def step(self, position: Dict[str, str]) -> None:
        with self._lock:
            error = self._error
            notices, self._notices = self._notices, []
        if error is not None:
            raise ContractError('experiment runtime failed') from error
        for event, details in notices:
            self.notice(event, details)

    def next_deadline(self) -> None:
        return None  # Timer servicing is exclusively in the Runtime process.

    def end(self, reason: str) -> None:
        self.runtime.request_stop()
        self.on_end()

    def execute(
        self, request: Dict[str, Any], position: Dict[str, str], receipt_index: str
    ) -> DeferredResult:
        """Forward control.wait/control.finish to the Runtime; the result arrives later."""
        name = 'wait' if request['tool'] == 'control.wait' else 'finish'
        data = {k: request[k] for k in ('work_id', 'epoch')}
        if name == 'wait':
            data.update(
                request_id=request['request_id'], conditions=request['arguments']['conditions']
            )
        account = self.capture('account.snapshot')['execution'].account
        budget_data = (
            dict(work_state='waiting', wait_id='x' * WAIT_ID_BUDGET)
            if name == 'wait'
            else dict(work_state='idle', next_due_ns='9' * DUE_TEXT_BUDGET)
        )
        response_size(
            success_response(
                request,
                receipt_index,
                cutoff(position, account.sequence, self.binding['source_manifest_ref']),
                budget_data,
            ),
            self.binding['limits']['tool_response_bytes'],
        )
        # Control tools themselves are excluded from outstanding operations.
        self.delivered(request['request_id'])
        source = self.runtime.submit(name, data)
        result: Future[Any] = Future()

        def complete(future: Future[Any]) -> None:
            try:
                response = success_response(
                    request,
                    receipt_index,
                    cutoff(position, account.sequence, self.binding['source_manifest_ref']),
                    future.result(),
                )
                response_size(response, self.binding['limits']['tool_response_bytes'])
                result.set_result(response)
            except ContractError as exc:
                result.set_result(
                    error_response(
                        request,
                        receipt_index,
                        position,
                        ToolError(getattr(exc, 'code', 'INVALID_ARGUMENT'), str(exc)),
                    )
                )
            except Exception as exc:
                _log.warning('Handled failure in complete', exc_info=True)
                result.set_exception(exc)

        source.add_done_callback(complete)
        return DeferredResult(result)
