"""Read-only market commands shared by all CLI session modes."""

from __future__ import annotations

from dataclasses import asdict
import json
from typing import TYPE_CHECKING, Any, Callable, List

if TYPE_CHECKING:  # Annotation only.
    from .interactive import CommandRegistry


def emit_market_value(value: Any) -> None:
    """Print a market dataclass (or list of them) as one JSON line, with time of day."""
    result: Any = (
        asdict(value) if hasattr(value, '__dataclass_fields__') else [asdict(v) for v in value]
    )
    if hasattr(value, 'time_of_day'):
        result['time_of_day'] = value.time_of_day
    elif hasattr(value, 'snapshot'):
        result['snapshot']['time_of_day'] = value.snapshot.time_of_day
    print(json.dumps(result, ensure_ascii=False), flush=True)


def register_market_queries(registry: CommandRegistry, market: Any) -> None:
    """Add book/trades/bars/news commands reading ``market`` (a market session)."""

    def query(method: Callable[[], Any]) -> Callable[[List[str]], None]:
        def handle(words: List[str]) -> None:
            emit_market_value(method())

        return handle

    registry.add('book', query(market.book))
    registry.add('trades', query(market.trades))
    registry.add('bars', query(market.bars))
    registry.add('news', query(market.news))
