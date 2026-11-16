"""Immutable financial outcomes; wire formatting runs in the tools process."""

from __future__ import annotations

from dataclasses import dataclass
from concurrent.futures import Future
from typing import TYPE_CHECKING, Any, Dict, Optional
from sim_platform.experiments.execution.results import DeferredResult
from ..protocol.contracts import ToolError
from ..reads.formats import order
from ..protocol.responses import cutoff, success_response, error_response

if TYPE_CHECKING:  # Annotation only.
    from sim_platform.domain.models import OrderSnapshot


@dataclass(frozen=True)
class FinancialCapture:
    """The original outcome of one financial tool request, formatted later off-owner."""

    request: dict
    position: dict
    receipt_index: str
    account_sequence: int = 0
    outcome: str = ''
    order: Optional[OrderSnapshot] = None
    version: int = 0
    reason_code: Optional[str] = None
    error_code: Optional[str] = None
    error_message: str = ''
    original: Optional[FinancialCapture] = None


def format_financial(captured: FinancialCapture, source_id: str) -> Dict[str, Any]:
    """Pure formatting of the original outcome, never a new financial action."""
    if captured.error_code:
        return error_response(
            captured.request,
            captured.receipt_index,
            captured.position,
            ToolError(captured.error_code, captured.error_message),
        )
    if captured.request['tool'] == 'requests.get':
        data = dict(
            target_request_id=captured.request['arguments']['target_request_id'],
            found=captured.original is not None,
            original_response=(
                format_financial(captured.original, source_id)
                if captured.original is not None
                else None
            ),
        )
    else:
        data = dict(
            outcome=captured.outcome,
            order=order(captured.order, captured.version) if captured.order else None,
        )
        if captured.request['tool'] in ('orders.submit_limit', 'orders.submit_market'):
            data['reason_code'] = captured.reason_code
    observed = cutoff(captured.position, captured.account_sequence, source_id)
    return success_response(captured.request, captured.receipt_index, observed, data)


@dataclass(frozen=True)
class RetainedFinancialResult(DeferredResult):
    """A committed financial outcome formatted by the output thread when delivered."""

    source_id: str
    maximum: int

    def resolve(self) -> Dict[str, Any]:
        from ..protocol.responses import response_size

        result = format_financial(self.future.result(), self.source_id)
        response_size(result, self.maximum)
        return result


def financial_fallback(
    captured: FinancialCapture, binding: Dict[str, Any]
) -> RetainedFinancialResult:
    """Commit/transport race: output thread formats outcome, owner never retries."""
    future: Future[Any] = Future()
    future.set_result(captured)
    return RetainedFinancialResult(
        future, binding['source_manifest_ref'], binding['limits']['tool_response_bytes']
    )
