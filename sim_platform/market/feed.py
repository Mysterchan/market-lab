"""Native BinaryFILE source controller and finite sequenced-stream fixture adapter."""

from __future__ import annotations

from typing import Any, Dict, Iterable, Iterator, Optional

from .models import FeedRecord, MarketError


class BinaryFileFeed:
    """Native-owned file source; Python never reads or splits BinaryFILE bytes."""

    integrity = 'ordered_unverified'  # BinaryFILE has no transport sequence numbers.

    def __init__(self, path: Any, allow_implicit_eof: bool = False) -> None:
        self.path = path
        # Accept a file that ends without an end-of-messages record.
        self.allow_implicit_eof = allow_implicit_eof
        self._engine: Any = None

    def bind(self, engine: Any) -> None:
        """Open the file in the native engine; a feed can be bound only once."""
        if self._engine is not None:
            raise MarketError('BinaryFileFeed is already bound')
        self._engine = engine
        engine.open_file(self.path, self.allow_implicit_eof)

    def peek(self) -> Optional[Dict[str, Any]]:
        # Only sequence/time cross the ABI; payload stays in C++ pending storage.
        return self._engine.peek_file()

    def advance(self) -> Dict[str, Any]:
        """Apply the next file record in the native engine and return its decoded result."""
        return self._engine.next_file()

    def close(self) -> None:
        if self._engine is not None:
            self._engine.close()


class SequencedFeed:
    """Finite simulated stream; sequence starts at 1, no stream recovery."""

    integrity = 'sequenced'

    def __init__(self, records: Iterable[FeedRecord]) -> None:
        self.records = iter(records)

    def __iter__(self) -> Iterator[FeedRecord]:
        return self.records

    def close(self) -> None:
        close = getattr(self.records, 'close', None)
        if close:
            close()
