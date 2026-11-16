"""Owner-local immutable pages; bounded cursor storage, no hash or database."""

from __future__ import annotations

from collections import OrderedDict
from typing import Any, Dict, Hashable, List, Optional, Tuple, cast
from uuid import uuid4
from sim_platform.domain.json_values import copy_json
from ..protocol.contracts import ToolError


# Saved pages kept when no capacity is given.
DEFAULT_PAGE_CAPACITY = 16

# A saved page: (query identity, all items, offset, cutoff, continuation token or None).
SavedPage = Tuple[Hashable, List[Any], int, Any, Optional[str]]


class Pages:
    """Saved result lists behind continuation cursors; the oldest are dropped first.

    Each ReadFormatter owns one inside the tools child for the run.
    """

    def __init__(self, capacity: int = DEFAULT_PAGE_CAPACITY) -> None:
        self.capacity = capacity
        self._pages: OrderedDict[str, SavedPage] = OrderedDict()

    def _save(self, identity: Hashable, items: List[Any], offset: int, cutoff: Any) -> str:
        token = 'page-' + uuid4().hex
        self._pages[token] = (identity, items, offset, copy_json(cutoff), None)
        while len(self._pages) > self.capacity:
            self._pages.popitem(last=False)
        return token

    def read(
        self,
        identity: Hashable,
        items: List[Any],
        limit: int,
        cutoff: Any,
        cursor: Optional[str] = None,
    ) -> Tuple[Dict[str, Any], Any]:
        """One page of ``items`` (or of the cursor's saved list) and the pinned cutoff."""
        offset = 0
        existing = None
        if cursor is not None:
            page = self._pages.get(cursor)
            if page is None or page[0] != identity:
                raise ToolError('CURSOR_INVALID', 'cursor expired or belongs to another query')
            _, items, offset, cutoff, existing = page
        end = offset + limit
        token = None
        if end < len(items):
            if existing is not None:
                if existing not in self._pages:
                    raise ToolError('CURSOR_INVALID', 'continuation expired')
                token = existing
            else:
                token = self._save(identity, items, end, cutoff)
                if cursor in self._pages:
                    self._pages[cast(str, cursor)] = (identity, items, offset, cutoff, token)
        return dict(items=copy_json(items[offset:end]), next_cursor=token), copy_json(cutoff)
