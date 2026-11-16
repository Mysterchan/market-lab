"""Worker-only read handlers: captured values in, wire result out.

No live capture callback, request validation, execution write or storage access.
Each tool has a named handler; only pagination and response assembly are shared.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Tuple

from sim_platform.domain.json_values import copy_json, encode
from ..protocol.contracts import ToolError, counter, IMPLEMENTED_TOOLS
from .formats import PRICE_SCALE, quote, level, trade, account, order, money
from .history import sliced_bars
from .paging import Pages
from ..protocol.responses import cutoff, success_response, error_response, response_size

if TYPE_CHECKING:  # Annotation only.
    from .capture import CapturedRead

# A capture with more orders than this is refused.
MAX_ORDERS = 1000

# A handler result: the tool's data and the (possibly pinned) cutoff.
Handled = Tuple[Any, Dict[str, Any]]


class ReadFormatter:
    """Formats captured read values into tool responses, inside the tools child.

    The tools process creates one per run from the tool binding; it keeps the
    continuation pages for paged tools.
    """

    def __init__(self, binding: Dict[str, Any]) -> None:
        self.binding = copy_json(binding)
        self.pages = Pages(binding['limits']['event_buckets'])

    def _orders(self, captured: CapturedRead, status: str) -> List[Dict[str, Any]]:
        values = captured.execution.account.orders
        if len(values) > MAX_ORDERS:
            raise ToolError('DATA_UNAVAILABLE', 'order history exceeds the supported window')
        return [
            order(value, captured.order_versions[value.order_id])
            for value in values
            if status == 'all' or value.status in ('accepted', 'partial')
        ]

    def _page(
        self,
        request: Dict[str, Any],
        items: List[Any],
        observed: Dict[str, Any],
        limit: Optional[int] = None,
    ) -> Handled:
        arguments = request['arguments']
        identity = (
            request['work_id'],
            request['epoch'],
            request['tool'],
            encode({k: v for k, v in arguments.items() if k != 'cursor'}),
        )
        return self.pages.read(
            identity,
            items,
            arguments['limit'] if limit is None else limit,
            observed,
            arguments.get('cursor'),
        )

    def _quote(
        self,
        request: Dict[str, Any],
        captured: CapturedRead,
        history: Dict[str, Any],
        observed: Dict[str, Any],
    ) -> Handled:
        trades = history['trades']
        last = trades[-1] if trades else None
        missing = 'not_observed' if history['trades_complete'] else 'not_retained'
        return quote(captured.market, last, missing), observed

    def _depth(
        self,
        request: Dict[str, Any],
        captured: CapturedRead,
        history: Dict[str, Any],
        observed: Dict[str, Any],
    ) -> Handled:
        market, levels = captured.market, request['arguments']['levels']
        return (
            dict(
                symbol=market.symbol,
                bids=[level(v) for v in market.bids[:levels]],
                asks=[level(v) for v in market.asks[:levels]],
                bid_levels=market.bid_levels,
                ask_levels=market.ask_levels,
                book_time_ns=None if market.book_time_ns is None else str(market.book_time_ns),
            ),
            observed,
        )

    def _trades(
        self,
        request: Dict[str, Any],
        captured: CapturedRead,
        history: Dict[str, Any],
        observed: Dict[str, Any],
    ) -> Handled:
        return self._page(request, [trade(value) for value in history['trades']], observed)

    @staticmethod
    def _bar_continuation(data: Dict[str, Any]) -> Dict[str, Any]:
        data['next_start_ns'] = data['items'][-1]['end_ns'] if data['next_cursor'] else None
        return data

    def _bars(
        self,
        request: Dict[str, Any],
        captured: CapturedRead,
        history: Dict[str, Any],
        observed: Dict[str, Any],
    ) -> Handled:
        args = request['arguments']
        start, end = counter(args['start_ns']), counter(args['end_ns'])
        if end > int(observed['market_position']['time_ns']):
            raise ToolError('FUTURE_DATA', 'bar range extends beyond the executed cutoff')
        data, observed = self._page(request, sliced_bars(history, start, end), observed)
        return self._bar_continuation(data), observed

    def _news(
        self,
        request: Dict[str, Any],
        captured: CapturedRead,
        history: Dict[str, Any],
        observed: Dict[str, Any],
    ) -> Handled:
        """Visible publications, newest first; the capture already applied the cutoff."""
        records = sorted(
            history.get('news') or (), key=lambda n: n['available_time_ns'], reverse=True
        )
        return (
            dict(
                items=[
                    dict(
                        record_id=n['record_id'],
                        headline=n['headline'],
                        period=n['period'],
                        revision=n['revision'],
                        available_time_ns=str(n['available_time_ns']),
                        provenance=n['provenance'],
                    )
                    for n in records[: request['arguments']['limit']]
                ]
            ),
            observed,
        )

    def _fundamentals(
        self,
        request: Dict[str, Any],
        captured: CapturedRead,
        history: Dict[str, Any],
        observed: Dict[str, Any],
    ) -> Handled:
        """Point-in-time fundamentals captured at the cutoff (market.fundamentals)."""
        values = history.get('fundamentals')
        if values is None:
            raise ToolError('DATA_UNAVAILABLE', 'no fundamentals are attached to this experiment')
        return (
            dict(
                symbol=captured.market.symbol,
                metrics={
                    name: [
                        dict(row, available_time_ns=str(row['available_time_ns'])) for row in rows
                    ]
                    for name, rows in values.items()
                },
            ),
            observed,
        )

    def _documents(
        self,
        request: Dict[str, Any],
        captured: CapturedRead,
        history: Dict[str, Any],
        observed: Dict[str, Any],
    ) -> Handled:
        """Search results or one page, captured at the cutoff (market.documents)."""
        value = history.get('documents')
        if value is None:
            raise ToolError('DATA_UNAVAILABLE', 'no documents are attached to this experiment')
        if 'error' in value:
            raise ToolError('DATA_UNAVAILABLE', value['error'])
        data = copy_json(value)
        for item in data.get('items', ()):
            item['available_time_ns'] = str(item['available_time_ns'])
        return data, observed

    def _journal(
        self,
        request: Dict[str, Any],
        captured: CapturedRead,
        history: Dict[str, Any],
        observed: Dict[str, Any],
    ) -> Handled:
        """The note itself is kept by the operation log with this request."""
        return dict(recorded=True, characters=len(request['arguments']['text'])), observed

    def _account(
        self,
        request: Dict[str, Any],
        captured: CapturedRead,
        history: Dict[str, Any],
        observed: Dict[str, Any],
    ) -> Handled:
        return account(captured.execution), observed

    def _orders_list(
        self,
        request: Dict[str, Any],
        captured: CapturedRead,
        history: Dict[str, Any],
        observed: Dict[str, Any],
    ) -> Handled:
        return self._page(request, self._orders(captured, request['arguments']['status']), observed)

    def _order_get(
        self,
        request: Dict[str, Any],
        captured: CapturedRead,
        history: Dict[str, Any],
        observed: Dict[str, Any],
    ) -> Handled:
        order_id = request['arguments']['order_id']
        data = next(
            (item for item in self._orders(captured, 'all') if item['order_id'] == order_id), None
        )
        if data is None:
            raise ToolError('ORDER_NOT_FOUND', 'order is not visible in this account')
        return data, observed

    def _state(
        self,
        request: Dict[str, Any],
        captured: CapturedRead,
        history: Dict[str, Any],
        observed: Dict[str, Any],
    ) -> Handled:
        orders: Dict[str, Any] = dict(items=[], next_cursor=None)
        limit = request['arguments']['order_limit']
        if limit:
            order_request = dict(
                request, tool='orders.list', arguments=dict(limit=limit, status='open')
            )
            orders, _ = self._page(order_request, self._orders(captured, 'open'), observed)
        current_quote, _ = self._quote(request, captured, history, observed)
        return (
            dict(quote=current_quote, account=account(captured.execution), orders=orders),
            observed,
        )

    def _describe(
        self,
        request: Dict[str, Any],
        captured: CapturedRead,
        history: Dict[str, Any],
        observed: Dict[str, Any],
    ) -> Handled:
        market, execution = captured.market, captured.execution
        names = (
            'symbol',
            'start_ns',
            'end_ns',
            'source_manifest_ref',
            'execution_config_ref',
            'limits',
        )
        data = {name: self.binding[name] for name in names}
        data['symbols'] = list(self.binding.get('symbols') or [self.binding['symbol']])
        data.update(
            currency='USD',
            price_scale=PRICE_SCALE,
            granted_tools=['environment.describe']
            + sorted(
                (set(self.binding['granted_tools']) & IMPLEMENTED_TOOLS) - {'environment.describe'}
            ),
        )
        data['execution_rules'] = dict(
            model=execution.config.model,
            latency_ns=str(execution.config.latency_ns),
            fee_per_share_usd=money(execution.config.fee_per_share),
            max_order_quantity=execution.config.max_order_quantity,
            short_selling=False,
            leverage=False,
        )
        data['source'] = dict(
            scope='single_venue',
            status=market.status,
            integrity=market.integrity,
            source_time_ns=str(market.time_ns),
            system_event=market.system_event,
        )
        return data, observed

    def _read(
        self,
        request: Dict[str, Any],
        captured: CapturedRead,
        history: Dict[str, Any],
        observed: Dict[str, Any],
    ) -> Handled:
        """Only paged continuation bypasses its normal tool handler."""
        if request['arguments'].get('cursor') is not None:
            data, pinned = self._page(request, [], observed)
            if request['tool'] == 'market.bars':
                data = self._bar_continuation(data)
            return data, pinned
        handlers: Dict[str, Callable[..., Handled]] = {
            'environment.describe': self._describe,
            'market.quote': self._quote,
            'market.depth': self._depth,
            'market.trades': self._trades,
            'market.bars': self._bars,
            'news.list': self._news,
            'fundamentals.get': self._fundamentals,
            'documents.search': self._documents,
            'documents.read': self._documents,
            'journal.write': self._journal,
            'account.snapshot': self._account,
            'orders.list': self._orders_list,
            'orders.get': self._order_get,
            'state.snapshot': self._state,
        }
        return handlers[request['tool']](request, captured, history, observed)

    def execute(self, captured: CapturedRead) -> Dict[str, Any]:
        """Format an ingress-validated capture; check data availability and output bytes.

        Unexpected metadata/formatter errors propagate to the pipeline fault.
        No request revalidation or new live market read occurs on this worker.
        """
        request, position = captured.request, dict(captured.position)
        try:
            history = captured.history
            if hasattr(history, 'materialize'):
                history = history.materialize()
            observed = cutoff(
                position, captured.execution.account.sequence, self.binding['source_manifest_ref']
            )
            data, observed = self._read(request, captured, history, observed)
            response = success_response(request, captured.receipt_index, observed, data)
            response_size(response, self.binding['limits']['tool_response_bytes'])
            return response
        except ToolError as error:
            return error_response(request, captured.receipt_index, position, error)
