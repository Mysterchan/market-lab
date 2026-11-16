"""Owner routing over a persistent tools process; no local formatting worker."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Dict, Mapping, Optional, Union, cast

from sim_platform.domain.json_values import copy_json
from sim_platform.agents.tools.protocol.contracts import (
    validate_request,
    TRADE_TOOLS,
    CONTROL_TOOLS,
    ToolError,
)
from sim_platform.agents.tools.reads.capture import capture_read
from sim_platform.agents.tools.protocol.responses import error_response
from sim_platform.agents.tools.worker.host import ToolProcessHost
from sim_platform.agents.tools.server.result_history import ToolResultHistory

if TYPE_CHECKING:  # Annotation only.
    from sim_platform.agents.tools.reads.capture import CapturedRead
    from sim_platform.agents.tools.trading.commands import TradingTools
    from sim_platform.agents.tools.trading.financial import FinancialCapture

# A tool's eventual result: a response, or a DeferredResult resolved on output.
ToolOutcome = Any


# Captures what a market-route tool may see: (tool, cutoff time, arguments) to values.
MarketCapture = Callable[[str, int, Dict[str, Any]], Mapping[str, Any]]


class AgentTools:
    """The App-side tool router for one Agent run, called on the financial owner.

    Assembly creates it with the run's tool binding, owner capture and trading
    tools; it starts and owns the tools process, and close() stops it. ``controls``
    is the run's RuntimeBinding, set after construction.

    Args:
        binding: Tool binding: run/work identity, symbol, grants, limits, references.
        capture: Captures detached owner values for a tool name.
        receipt_index: Returns the current command's receipt index.
        trading: Financial tool commands for the run.
    """

    def __init__(
        self,
        binding: Dict[str, Any],
        capture: Any,
        receipt_index: Callable[[], str],
        trading: TradingTools,
    ) -> None:
        self.binding = copy_json(binding)
        self._capture, self._receipt_index = capture, receipt_index
        self._trading = trading
        self._worker = ToolProcessHost(binding)
        # The run's RuntimeBinding (application package, which agents may not import);
        # None until assembly binds it.
        self.controls: Any = None
        self._market_capture: Optional[MarketCapture] = None
        self._history = ToolResultHistory(
            binding['limits']['tool_request_bytes'], binding['limits']['tool_response_bytes']
        )

    def validate(self, request: Any) -> Dict[str, Any]:
        """Ingress only: validate schema/grants before touching owner capabilities."""
        return validate_request(request, self.binding, check_work=False)

    def has_retained_request(self, request: Dict[str, Any]) -> bool:
        """Owner replay checks exact content; existing IDs never cause new effects."""
        return self._history.contains(request['request_id'])

    def delivered(self, request_id: str, result: Any) -> None:
        self._history.delivered(request_id, result)

    def bind_market_capture(self, capture: MarketCapture) -> None:
        if self._market_capture is not None:
            raise ToolError('INVALID_ARGUMENT', 'market read capture already bound')
        self._market_capture = capture

    def _capture_market(
        self, request: Dict[str, Any], position: Dict[str, str], receipt_index: str
    ) -> ToolOutcome:
        from sim_platform.agents.tools.reads.capture import CapturedRead
        from types import MappingProxyType

        # market_read checks that the capture is bound before calling this.
        values = cast(MarketCapture, self._market_capture)(
            request['tool'], int(position['time_ns']), request['arguments']
        )
        captured = CapturedRead(
            request,
            MappingProxyType(dict(position)),
            values['market'],
            values['execution'],
            values['history'],
            MappingProxyType({}),
            receipt_index,
        )
        return self._worker.submit(captured)

    def market_read(
        self, request: Dict[str, Any], position: Dict[str, str], receipt_index: str
    ) -> ToolOutcome:
        """Capture a granted immutable query; preserve normal tool error envelopes."""

        def failed(error: ToolError) -> Dict[str, Any]:
            return error_response(request, receipt_index, position, error)

        try:
            if self._market_capture is None:
                raise ToolError('TOOL_UNAVAILABLE', 'prepared market reads are not bound')
            if self.controls is not None:
                self.controls.validate_identity(request)
            return self._history.capture(
                request,
                lambda: self._capture_market(request, position, receipt_index),
                failed,
                release_on_failure=True,
            )
        except ToolError as error:
            return failed(error)

    def dispatch(self, request: Dict[str, Any], position: Dict[str, str]) -> ToolOutcome:
        return self.capture_and_submit(request, position)

    def _perform(self, request: Dict[str, Any], position: Dict[str, str]) -> ToolOutcome:
        """Owner routing only: controls, raw financial capture, or read capture."""
        if self.controls is not None:
            self.controls.validate_identity(request)
        if request['tool'] in CONTROL_TOOLS:
            return self.controls.execute(request, position, self._receipt_index())
        self._worker.require_available()
        captured: Union[CapturedRead, FinancialCapture]
        if request['tool'] in TRADE_TOOLS or self._trading.has_request(request['request_id']):
            captured = self._trading.execute(request, position)
        else:
            captured = capture_read(request, position, self._capture, self._receipt_index())
        return self._worker.submit(captured)

    def _tool_error(
        self, request: Dict[str, Any], position: Dict[str, str], error: ToolError
    ) -> ToolOutcome:
        retained = (
            self._trading.has_request(request['request_id']) or request['tool'] == 'requests.get'
        )
        if error.code == 'TOOL_UNAVAILABLE' and retained:
            from sim_platform.agents.tools.trading.financial import financial_fallback

            return financial_fallback(self._trading.execute(request, position), self.binding)
        return error_response(request, self._receipt_index(), position, error)

    def capture_and_submit(self, request: Dict[str, Any], position: Dict[str, str]) -> ToolOutcome:
        """Replay or reserve → perform → retain; owner never waits for process IO."""
        return self._history.capture(
            request,
            lambda: self._perform(request, position),
            lambda error: self._tool_error(request, position, error),
        )

    def snapshot(self) -> Dict[str, Any]:
        return self._worker.snapshot()

    def play(self) -> Dict[str, Any]:
        return self._worker.play()

    def pause(self) -> Dict[str, Any]:
        return self._worker.pause()

    def cancel_pending(self) -> None:
        self._worker.cancel_pending()

    def close(self) -> None:
        self._worker.close()
