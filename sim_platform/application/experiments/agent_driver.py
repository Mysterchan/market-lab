"""Detached tool capture and ordered JSON output over existing execution.

Market-only reads use the immutable reader; financial capture runs on the owner.
Only the output stage writes JSON and archives it.
"""

from __future__ import annotations

from sim_platform.domain.models import ContractError, MarketOrder
from dataclasses import asdict
from typing import TYPE_CHECKING, Any, Callable, Dict, Optional, Tuple, Union
from .contracts import ToolCapture

if TYPE_CHECKING:  # Annotation only.
    from sim_platform.agents.operation_log import AgentOperationLog
    from sim_platform.domain.models import Intent
    from sim_platform.execution.models import ExecutionSnapshot
    from sim_platform.market.documents import DocumentStore
    from sim_platform.market.fundamentals import FundamentalStore
    from sim_platform.experiments.execution.market import LocalMarketExecution
    from sim_platform.experiments.execution.timeline.runner import TimelineReader

# A capture holding more orders than this is refused (except for cancellation).
MAX_CAPTURED_ORDERS = 1000


class AgentPipelineOutput:
    """The pipeline's output sink: recent activity, and the Agent log when recording.

    Created at assembly (first without a recorder, then rebound with one); it runs on
    the pipeline's output thread. On the 'end' message it finishes the log and, if
    configured, archives it.

    Args:
        recorder: Agent operation log, or None before tools are bound.
        archive: Archives the finished log file by path.
    """

    def __init__(
        self,
        recorder: Optional[AgentOperationLog] = None,
        archive: Optional[Callable[[Any], Any]] = None,
    ) -> None:
        if archive is not None and recorder is None:
            raise ContractError('archive requires an agent recorder')
        self.recorder, self.archive = recorder, archive
        from sim_platform.experiments.activity import RecentActivity

        self.activity = RecentActivity()

    def __call__(self, message: Dict[str, Any]) -> None:
        self.activity.record(message)
        if self.recorder is not None:
            if message['kind'] == 'result':
                self.recorder.record_tool(
                    message['request'],
                    message['result'],
                    message['position'],
                    receipt_target_ns=message['receipt_target_ns'],
                )
            elif message['kind'] == 'notice':
                self.recorder.record_control(
                    message['event'], message['position'], message['details']
                )
            elif message['kind'] == 'control':
                self.recorder.record_control(
                    message['event'], message['position'], {'applied': message['applied']}
                )
            else:
                self.recorder.record_control(
                    'end',
                    message['position'],
                    {'reason': message['reason'], 'evaluation': message.get('evaluation')},
                )
                self.recorder.finish(message['status'])
                if self.archive is not None:
                    self.archive(self.recorder.path)


class AgentToolCapture:
    """Called exclusively by the pipeline owner at its operation boundary.

    Created per Agent run during assembly; it borrows the market driver and commits
    orders through the bound commit_decision. It keeps the observed order versions.
    """

    def __init__(
        self,
        driver: LocalMarketExecution,
        commit_decision: Callable[..., Tuple[Any, ExecutionSnapshot]],
        require_owner: Callable[[], None],
    ) -> None:
        self.driver = driver
        self._commit_decision, self._require_owner = commit_decision, require_owner
        from sim_platform.agents.tools.reads.capture import OrderVersions

        self.versions = OrderVersions()

    def _financial(
        self, action: Dict[str, Any], position: Dict[str, Any]
    ) -> Tuple[Any, Dict[str, Any]]:
        """Commit one order action; return its result and the post-trade capture."""
        self._require_owner()
        try:
            result, execution = self._commit_decision(
                self.driver.market.snapshot, action=action, position=position
            )
        except Exception:
            # The pipeline ends on this error. Do not run a second financial
            # transaction to cancel/finish an account whose storage is untrusted.
            self.driver.execution.abandon()
            raise
        selected = tuple(
            order for order in execution.account.orders if order.order_id == result.order_id
        )
        return result, dict(execution=execution, order_versions=self.versions.observe(selected))

    def submit(
        self, intent: Union[Intent, MarketOrder], position: Dict[str, Any]
    ) -> Tuple[Any, Dict[str, Any]]:
        """Commit a limit order (Intent) or a market order (MarketOrder)."""
        if isinstance(intent, MarketOrder):
            return self._financial(dict(kind='submit_market', order=asdict(intent)), position)
        return self._financial(dict(kind='submit', intent=asdict(intent)), position)

    def cancel(self, order_id: str, position: Dict[str, Any]) -> Tuple[Any, Dict[str, Any]]:
        return self._financial(dict(kind='cancel', order_id=order_id), position)

    def capture_at(self, tool: str, position: Dict[str, Any]) -> ToolCapture:
        """Capture for ``tool`` with market data cut at the position's time when prepared."""
        values = self(tool)
        if hasattr(self.driver.market, 'reader'):
            cutoff = int(position['time_ns'])
            reader = self.driver.market.reader
            from sim_platform.market.models import book_for

            snapshot = reader.snapshot_at(cutoff)
            # Several symbols: the current event's book, as a plain snapshot.
            values['market'] = book_for(snapshot, snapshot.symbol)
            values['history'] = reader.history.capture(
                reader.prefix(cutoff), cutoff, include_bars=tool == 'market.bars'
            )
        return values

    def __call__(self, tool: str) -> ToolCapture:
        with self.driver.execution.control_boundary():
            history = (
                self.driver.market.capture_agent_history(include_bars=tool == 'market.bars')
                if tool in ('market.quote', 'market.trades', 'market.bars', 'state.snapshot')
                else dict(
                    trades=(), bars=(), trades_complete=True, bars_complete=True, trade_start_ns=0
                )
            )
            execution = self.driver.execution.snapshot
            orders = (
                execution.account.orders
                if tool in ('orders.list', 'orders.get', 'state.snapshot', 'orders.cancel')
                else ()
            )
            if tool != 'orders.cancel' and len(orders) > MAX_CAPTURED_ORDERS:
                from sim_platform.agents.tools.protocol.contracts import ToolError

                raise ToolError('DATA_UNAVAILABLE', 'order history exceeds the supported window')
            return dict(
                market=self.driver.market.snapshot,
                execution=execution,
                order_versions=self.versions.observe(orders),
                history=history,
            )


class PreparedToolCapture:
    """Borrow immutable reader and fixed config only; no live account read on this path.

    Created per Agent run; it keeps the run's execution settings with the account's
    live fields (orders, positions, sequence) removed.
    """

    def __init__(
        self,
        reader: TimelineReader,
        execution_metadata: ExecutionSnapshot,
        fundamentals: Optional[FundamentalStore] = None,
        documents: Optional[DocumentStore] = None,
    ) -> None:
        from dataclasses import replace

        # Suspected bug: AccountSnapshot.sequence is declared int, but this copy stores None
        # to mark "no live account"; code reading sequence from it gets None.
        account = replace(
            execution_metadata.account,
            orders=(),
            positions=(),
            sequence=None,  # type: ignore[arg-type]
        )
        self._metadata = replace(execution_metadata, account=account, reserved_shares=())
        self._reader = reader
        self._fundamentals = fundamentals
        self._documents = documents

    def __call__(
        self, tool: str, cutoff: int, arguments: Optional[Dict[str, Any]] = None
    ) -> ToolCapture:
        """Capture what ``tool`` may see at ``cutoff``; nothing published later is included."""
        arguments = arguments or {}
        symbol_reader = getattr(self._reader, 'symbol_reader', None)
        if symbol_reader is not None:
            # Several symbols: read the named symbol's book and history.
            from sim_platform.market.models import book_for

            merged: Any = self._reader
            symbol = arguments.get('symbol') or merged.universe[0]
            local = symbol_reader(symbol)
            snapshot = book_for(self._reader.snapshot_at(cutoff), symbol)
            history = local.history.capture(
                local.prefix(cutoff), cutoff, include_bars=tool == 'market.bars'
            )
        else:
            snapshot = self._reader.snapshot_at(cutoff)
            history = self._reader.history.capture(
                self._reader.prefix(cutoff), cutoff, include_bars=tool == 'market.bars'
            )
        if tool == 'news.list':
            news_at = getattr(self._reader, 'news_at', None)
            # Plain values: the capture crosses process IPC to the tools child.
            visible = news_at(cutoff, arguments['limit']) if news_at else ()
            history = dict(history, news=tuple(asdict(record) for record in visible))
        elif tool == 'fundamentals.get':
            history = dict(
                history,
                fundamentals=(
                    None
                    if self._fundamentals is None
                    else self._fundamentals.as_of(
                        cutoff, snapshot.symbol, arguments.get('metrics'), arguments['periods']
                    )
                ),
            )
        elif tool in ('documents.search', 'documents.read'):
            history = dict(history, documents=self._document_capture(tool, cutoff, arguments))
        return dict(market=snapshot, execution=self._metadata, history=history)

    def _document_capture(
        self, tool: str, cutoff: int, arguments: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Search results or one page, or an ``error`` the formatter reports."""
        if self._documents is None:
            return None
        try:
            if tool == 'documents.search':
                return dict(
                    items=self._documents.search(cutoff, arguments['query'], arguments['limit'])
                )
            return self._documents.read(
                cutoff, arguments['document_id'], arguments.get('offset', 0)
            )
        except ContractError as exc:
            return dict(error=str(exc))
