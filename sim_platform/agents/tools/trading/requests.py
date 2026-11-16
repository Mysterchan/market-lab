"""Owner-held immutable financial captures; no wire formatting, SQL or eviction."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Optional, Tuple

from sim_platform.domain.json_values import encode
from ..protocol.contracts import ToolError

MAX_FINANCIAL_REQUESTS = 10000
MAX_RECEIPT_BYTES = 64 * 1024 * 1024


if TYPE_CHECKING:  # Annotation only.
    from .financial import FinancialCapture


class FinancialRequestHistory:
    """Original financial outcomes by request ID; never evicted during a run.

    TradingTools owns one per run on the financial owner.
    """

    def __init__(self) -> None:
        self._records: Dict[str, Tuple[str, FinancialCapture]] = {}
        self._bytes = 0

    def contains(self, request_id: str) -> bool:
        return request_id in self._records

    def lookup(self, request_id: str) -> Optional[FinancialCapture]:
        record = self._records.get(request_id)
        return record[1] if record is not None else None

    def replay(self, request: Dict[str, Any]) -> Optional[FinancialCapture]:
        """Same ID/content returns the original result; changed content conflicts."""
        previous = self._records.get(request['request_id'])
        if previous is None:
            return None
        if previous[0] != encode(request):
            raise ToolError('REQUEST_ID_CONFLICT', 'request ID reused with different content')
        return previous[1]

    def ensure_capacity(self, request: Dict[str, Any], response_bytes: int) -> None:
        """Reserveable byte bound checked before financial effects; never evict results."""
        required = len(encode(request).encode('utf-8')) + response_bytes
        if (
            len(self._records) >= MAX_FINANCIAL_REQUESTS
            or self._bytes + required > MAX_RECEIPT_BYTES
        ):
            raise ToolError('QUEUE_FULL', 'financial request history capacity exhausted')

    def remember(
        self, request: Dict[str, Any], response: FinancialCapture, response_bytes: int
    ) -> None:
        self.ensure_capacity(request, response_bytes)
        canonical = encode(request)
        self._records[request['request_id']] = canonical, response
        self._bytes += len(canonical.encode('utf-8')) + response_bytes
