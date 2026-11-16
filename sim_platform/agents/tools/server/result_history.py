"""Bounded in-memory original tool results; no SQL, hashes or crash recovery.

Reserve an upper bound before side effects. Ordered output replaces it with
actual request/result JSON bytes off-owner. Locks protect only bookkeeping.
"""

from __future__ import annotations

from threading import RLock
from typing import Any, Callable, Dict, Optional, Tuple
from sim_platform.domain.json_values import encode
from sim_platform.agents.tools.protocol.contracts import ToolError


# Default bounds: retained results, and their total bytes (64 MiB).
DEFAULT_CAPACITY = 10000
DEFAULT_BYTE_LIMIT = 64 * 1024 * 1024


class ToolResultHistory:
    """Original tool results by request ID, so a retried request gets the same result.

    The AgentTools router owns one per run. Bytes are reserved before a tool runs
    and replaced by the actual size when the result is delivered.
    """

    def __init__(
        self,
        request_bound: int,
        response_bound: int,
        *,
        capacity: int = DEFAULT_CAPACITY,
        byte_limit: int = DEFAULT_BYTE_LIMIT,
    ) -> None:
        self.reservation = request_bound + response_bound
        self.capacity, self.byte_limit = capacity, byte_limit
        self._values: Dict[str, Tuple[Dict[str, Any], Any]] = {}
        self._budgets: Dict[str, int] = {}
        self._bytes = 0
        self._lock = RLock()

    def contains(self, request_id: str) -> bool:
        with self._lock:
            return request_id in self._values

    def lookup(self, request: Dict[str, Any]) -> Optional[Any]:
        with self._lock:
            value = self._values.get(request['request_id'])
            if value is not None and value[0] != request:
                raise ToolError('REQUEST_ID_CONFLICT', 'request ID reused with different content')
            return value[1] if value is not None else None

    def reserve(self, request: Dict[str, Any]) -> None:
        with self._lock:
            if request['request_id'] in self._budgets:
                raise ToolError(
                    'QUEUE_FULL', 'original request is still being captured; retry same request'
                )
            if (
                len(self._budgets) >= self.capacity
                or self._bytes + self.reservation > self.byte_limit
            ):
                raise ToolError('QUEUE_FULL', 'run result history capacity exhausted')
            self._budgets[request['request_id']] = self.reservation
            self._bytes += self.reservation

    def abandon(self, request_id: str) -> None:
        with self._lock:
            self._bytes -= self._budgets.pop(request_id, 0)

    def remember(self, request: Dict[str, Any], result: Any) -> None:
        with self._lock:
            self._values[request['request_id']] = request, result

    def capture(
        self,
        request: Dict[str, Any],
        perform: Callable[[], Any],
        error_result: Callable[[ToolError], Any],
        *,
        release_on_failure: bool = False,
    ) -> Any:
        """Reuse or reserve one result, retaining errors under the same request ID.

        Queue-full errors release reservations for a later retry. Unexpected financial
        failures retain their reservation because their effects may be unknown; immutable
        reads may explicitly release it. This method never performs or retries a trade.
        """
        reserved = False
        try:
            previous = self.lookup(request)
            if previous is not None:
                return previous
            self.reserve(request)
            reserved = True
            result = perform()
        except ToolError as error:
            result = error_result(error)
            if reserved and error.code == 'QUEUE_FULL':
                self.abandon(request['request_id'])
                return result
        except Exception:
            if reserved and release_on_failure:
                self.abandon(request['request_id'])
            raise
        if reserved:
            self.remember(request, result)
        return result

    def delivered(self, request_id: str, result: Any) -> None:
        with self._lock:
            value = self._values.get(request_id)
        if value is None:
            return
        # Potentially large JSON work stays on output, outside the owner lock.
        size = len(encode(value[0]).encode()) + len(encode(result).encode())
        with self._lock:
            old = self._budgets[request_id]
            self._budgets[request_id] = size
            self._bytes += size - old
