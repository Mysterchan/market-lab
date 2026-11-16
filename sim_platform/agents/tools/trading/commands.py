"""Owner-only financial transitions; pure captured outcomes go to the tools process.

Pre-effect budgets and original outcomes stay owner-held. Wire formatting,
encoding and IPC are never part of the normal financial commit callback.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Dict, Tuple, Union

from sim_platform.domain.models import Intent, MarketOrder
from ..protocol.contracts import MAX_INT64, ToolError, price_units
from ..reads.formats import money, rejection_code
from .requests import FinancialRequestHistory
from .financial import FinancialCapture
from ..protocol.responses import error_response, response_size, financial_response_bound

if TYPE_CHECKING:  # Annotation only.
    from sim_platform.execution.models import ExecutionSnapshot

# Commits one action: returns the execution result and the post-trade capture.
FinancialAction = Callable[..., Tuple[Any, Dict[str, Any]]]


class TradingTools:
    """Financial tool commands for one Agent run, called on the financial owner.

    Assembly creates it with the owner's capture and submit/cancel commits; it keeps
    each original outcome so a retried request never trades twice.

    Args:
        binding: Tool binding (symbol, limits and references).
        capture: Captures detached owner values for a tool name.
        submit: Commits a limit order: submit(intent, position).
        cancel: Commits a cancellation: cancel(order_id, position).
        receipt_index: Returns the current command's receipt index.
    """

    def __init__(
        self,
        binding: Dict[str, Any],
        capture: Callable[[str], Any],
        submit: FinancialAction,
        cancel: FinancialAction,
        receipt_index: Callable[[], str],
    ) -> None:
        self.binding, self.capture = binding, capture
        self.submit, self.cancel = submit, cancel
        self.receipt_index = receipt_index
        self.history = FinancialRequestHistory()

    def has_request(self, request_id: str) -> bool:
        return self.history.contains(request_id)

    def _outcome(
        self,
        request: Dict[str, Any],
        position: Dict[str, Any],
        snapshot: ExecutionSnapshot,
        **values: Any,
    ) -> FinancialCapture:
        return FinancialCapture(
            request, dict(position), self.receipt_index(), snapshot.account.sequence, **values
        )

    def _error(
        self, request: Dict[str, Any], position: Dict[str, Any], error: ToolError
    ) -> FinancialCapture:
        return FinancialCapture(
            request,
            dict(position),
            self.receipt_index(),
            error_code=error.code,
            error_message=str(error),
        )

    def _preflight(
        self,
        request: Dict[str, Any],
        position: Dict[str, Any],
        quantity: int,
        side: str,
        price: str,
    ) -> int:
        """Budget response and retained outcome before any financial side effect."""
        maximum = financial_response_bound(
            request,
            position,
            self.binding['source_manifest_ref'],
            str(request['arguments'].get('symbol', self.binding['symbol'])),
            quantity,
            side,
            price,
        )
        if maximum > self.binding['limits']['tool_response_bytes']:
            raise ToolError('OUTPUT_TOO_LARGE', 'response exceeds configured byte limit')
        self.history.ensure_capacity(request, maximum)
        return maximum

    def _lookup(self, request: Dict[str, Any], position: Dict[str, Any]) -> FinancialCapture:
        snapshot = self.capture('account.snapshot')['execution']
        return self._outcome(
            request,
            position,
            snapshot,
            original=self.history.lookup(request['arguments']['target_request_id']),
        )

    def _submit_limit(
        self, request: Dict[str, Any], position: Dict[str, Any]
    ) -> Tuple[FinancialCapture, int]:
        args = request['arguments']
        price = price_units(args['limit_price_usd'])
        maximum = self._preflight(
            request, position, args['quantity_shares'], args['side'], money(price)
        )
        intent = Intent(
            request['request_id'], args['symbol'], args['side'], args['quantity_shares'], price
        )
        return self._submitted(request, position, intent, maximum)

    def _submit_market(
        self, request: Dict[str, Any], position: Dict[str, Any]
    ) -> Tuple[FinancialCapture, int]:
        args = request['arguments']
        # The protection price is set at acceptance; budget the response for the largest.
        maximum = self._preflight(
            request, position, args['quantity_shares'], args['side'], money(MAX_INT64)
        )
        order = MarketOrder(
            request['request_id'], args['symbol'], args['side'], args['quantity_shares']
        )
        return self._submitted(request, position, order, maximum)

    def _submitted(
        self,
        request: Dict[str, Any],
        position: Dict[str, Any],
        intent: Union[Intent, MarketOrder],
        maximum: int,
    ) -> Tuple[FinancialCapture, int]:
        """Commit a new order and capture its outcome."""
        result, captured = self.submit(intent, position)
        snapshot = captured['execution']
        accepted = next(
            (value for value in snapshot.account.orders if value.order_id == result.order_id), None
        )
        outcome = self._outcome(
            request,
            position,
            snapshot,
            outcome='rejected' if result.status == 'rejected' else 'accepted',
            order=accepted,
            version=captured['order_versions'][accepted.order_id] if accepted else 0,
            reason_code=rejection_code(result.reason),
        )
        return outcome, maximum

    def _cancel(
        self, request: Dict[str, Any], position: Dict[str, Any]
    ) -> Tuple[FinancialCapture, int]:
        captured = self.capture('orders.cancel')
        snapshot = captured['execution']
        order_id = request['arguments']['order_id']
        value = next(
            (
                item
                for item in snapshot.account.orders
                if item.order_id == order_id and item.run_id == request['run_id']
            ),
            None,
        )
        if value is None:
            raise ToolError('ORDER_NOT_FOUND', 'order is not in this run')
        maximum = self._preflight(
            request, position, value.quantity, value.side, money(value.limit_price)
        )
        if value.status not in ('accepted', 'partial'):
            return (
                self._outcome(
                    request,
                    position,
                    snapshot,
                    outcome='already_terminal',
                    order=value,
                    version=captured['order_versions'][value.order_id],
                ),
                maximum,
            )
        result, captured = self.cancel(order_id, position)
        return (
            self._outcome(
                request,
                position,
                captured['execution'],
                outcome='cancelled',
                order=result,
                version=captured['order_versions'][result.order_id],
            ),
            maximum,
        )

    def _new_financial_request(
        self, request: Dict[str, Any], position: Dict[str, Any]
    ) -> Tuple[FinancialCapture, int]:
        missing = ToolError('ORDER_NOT_FOUND', 'order is not in this run')
        maximum = response_size(
            error_response(request, self.receipt_index(), position, missing),
            self.binding['limits']['tool_response_bytes'],
        )
        self.history.ensure_capacity(request, maximum)
        handlers: Dict[str, Callable[..., Tuple[FinancialCapture, int]]] = {
            'orders.submit_limit': self._submit_limit,
            'orders.submit_market': self._submit_market,
            'orders.cancel': self._cancel,
        }
        try:
            return handlers[request['tool']](request, position)
        except ToolError as error:
            if error.code in ('QUEUE_FULL', 'OUTPUT_TOO_LARGE'):
                raise
            return self._error(request, position, error), maximum

    def execute(self, request: Dict[str, Any], position: Dict[str, Any]) -> FinancialCapture:
        """Capture original outcome at commit; replay never submits a second action."""
        try:
            previous = self.history.replay(request)
            if previous is not None:
                return previous
            if request['tool'] == 'requests.get':
                return self._lookup(request, position)
            captured, maximum = self._new_financial_request(request, position)
        except ToolError as error:
            return self._error(request, position, error)
        self.history.remember(request, captured, maximum)
        return captured
