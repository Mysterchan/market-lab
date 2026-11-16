"""Bounded immutable-row projection of existing history, without IO or new aggregation."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any, Deque, Dict, Iterable, Sequence, Tuple

from .models import MarketTrade, PriceVolumeBar

# Bars are one minute wide.
INTERVAL = 60_000_000_000
# The window keeps this many of the most recent trades and bars.
TRADE_LIMIT = 1000
BAR_LIMIT = 100

# A stored trade: (sequence, time, match ID, price, quantity, printable, kind as bytes,
# time at which the trade was broken or a later sentinel).
TradeRow = Tuple[Any, ...]
# A stored bar: (start time, open, high, low, close, volume, count, notional).
BarRow = Tuple[Any, ...]


@dataclass(frozen=True)
class HistoryCapture:
    """A point-in-time copy of the window that can be turned into rows on any thread."""

    cutoff: int
    trades: Tuple[TradeRow, ...]
    bars: Tuple[BarRow, ...]
    trade_count: int
    bar_count: int

    def materialize(self) -> Dict[str, Any]:
        """Pure filtering/row construction, safe on a worker after the market changes."""
        cutoff = self.cutoff
        trades = tuple(
            MarketTrade(seq, stamp, match, price, quantity, printable, kind.decode())
            for seq, stamp, match, price, quantity, printable, kind, broken in self.trades
            if stamp <= cutoff and quantity and printable and broken > cutoff
        )
        bars = tuple(
            PriceVolumeBar(stamp, stamp + INTERVAL, cutoff, stamp + INTERVAL <= cutoff, *rest)
            for stamp, *rest in self.bars
        )
        return dict(
            trades=trades,
            bars=bars,
            trades_complete=self.trade_count <= TRADE_LIMIT,
            trade_start_ns=self.trades[0][1] if self.trades else 0,
            bars_complete=self.bar_count <= BAR_LIMIT,
        )


class HistoryWindow:
    """The most recent trades and bars, kept as immutable rows.

    ``trade_count`` and ``bar_count`` count everything ever added, so a reader can
    tell whether the bounded window still holds the complete history.
    """

    def __init__(
        self,
        trades: Iterable[TradeRow],
        bars: Iterable[BarRow],
        trade_count: int,
        bar_count: int,
    ) -> None:
        self.trades: Deque[TradeRow] = deque(trades, maxlen=TRADE_LIMIT)
        self.bars: Deque[BarRow] = deque(bars, maxlen=BAR_LIMIT)
        self.trade_count, self.bar_count = trade_count, bar_count

    def add_trade(self, row: TradeRow) -> None:
        self.trades.append(row)
        self.trade_count += 1

    def correct_trade(self, row: Sequence[Any]) -> None:
        """Replace the retained trade with the same match ID, for example when it is broken."""
        for index, previous in enumerate(self.trades):
            if previous[2] == row[2]:
                self.trades[index] = tuple(row)
                break

    def update_bar(self, row: Sequence[Any]) -> None:
        """Replace the latest bar if it has the same start time, otherwise append a new one."""
        row = tuple(row)
        if self.bars and self.bars[-1][0] == row[0]:
            self.bars[-1] = row
        else:
            self.bars.append(row)
            self.bar_count += 1

    def replace_bars(self, rows: Iterable[BarRow], count: int) -> None:
        self.bars = deque(rows, maxlen=BAR_LIMIT)
        self.bar_count = count

    def capture(self, cutoff: int, include_bars: bool) -> HistoryCapture:
        """Owner copies bounded tuple references; rows are replaced, never mutated."""
        return HistoryCapture(
            cutoff,
            tuple(self.trades),
            tuple(self.bars) if include_bars else (),
            self.trade_count,
            self.bar_count,
        )

    def snapshot(self, cutoff: int, include_bars: bool) -> Dict[str, Any]:
        return self.capture(cutoff, include_bars).materialize()
