"""Pure conversion from existing immutable snapshots to tool wire values."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence

if TYPE_CHECKING:  # Annotation only.
    from sim_platform.domain.models import OrderSnapshot
    from sim_platform.execution.models import ExecutionSnapshot
    from sim_platform.market.models import MarketSnapshot, MarketTrade, PriceVolumeBar

# Money units per USD.
PRICE_SCALE = 10_000


def money(value: int) -> str:
    """Money units as decimal USD text with four places."""
    sign = '-' if value < 0 else ''
    whole, fraction = divmod(abs(value), PRICE_SCALE)
    return f'{sign}{whole}.{fraction:04d}'


def rejection_code(reason: Optional[str]) -> Optional[str]:
    """Translate existing engine messages to the fixed tool wire enum."""
    if reason is None:
        return None
    codes = {
        'insufficient available cash including reservations and fees': 'INSUFFICIENT_CASH',
        'insufficient available shares; short selling is unsupported': 'INSUFFICIENT_SHARES',
        'order exceeds maximum quantity': 'ORDER_LIMIT_EXCEEDED',
        'symbol is outside the market session': 'SYMBOL_MISMATCH',
        'sell limit must cover per-share fee': 'LIMIT_BELOW_FEE',
    }
    if reason in codes:
        return codes[reason]
    return 'VALUE_OUT_OF_RANGE' if 'must be an integer' in reason else 'MARKET_NOT_TRADABLE'


def level(value: Sequence[int]) -> Dict[str, Any]:
    return dict(price_usd=money(value[0]), quantity_shares=value[1])


def trade(value: MarketTrade) -> Dict[str, Any]:
    return dict(
        sequence=str(value.sequence),
        time_ns=str(value.timestamp_ns),
        match_id=str(value.match_id),
        price_usd=money(value.price),
        quantity_shares=value.quantity,
        printable=value.printable,
        kind=value.kind,
    )


def quote(
    snapshot: MarketSnapshot, last: Optional[MarketTrade], last_missing: str
) -> Dict[str, Any]:
    missing: List[Dict[str, Any]] = []
    for name, values in (('bid', snapshot.bids), ('ask', snapshot.asks)):
        if not values:
            missing.append(
                dict(
                    field=name,
                    reason=(
                        'no_visible_liquidity'
                        if snapshot.book_time_ns is not None
                        else 'not_observed'
                    ),
                )
            )
    if last is None:
        missing.append(dict(field='last', reason=last_missing))
    return dict(
        symbol=snapshot.symbol,
        bid=level(snapshot.bids[0]) if snapshot.bids else None,
        ask=level(snapshot.asks[0]) if snapshot.asks else None,
        last=(
            None
            if last is None
            else dict(
                price_usd=money(last.price),
                quantity_shares=last.quantity,
                time_ns=str(last.timestamp_ns),
            )
        ),
        missing=missing,
        book_time_ns=None if snapshot.book_time_ns is None else str(snapshot.book_time_ns),
        integrity=snapshot.integrity,
        trading_state=snapshot.trading_state,
    )


def account(snapshot: ExecutionSnapshot) -> Dict[str, Any]:
    reserved = dict(snapshot.reserved_shares)
    positions = [
        dict(
            symbol=p.symbol,
            quantity_shares=p.quantity,
            available_shares=p.quantity - reserved.get(p.symbol, 0),
            reserved_shares=reserved.get(p.symbol, 0),
            cost_usd=money(p.cost),
        )
        for p in snapshot.account.positions
    ]
    return dict(
        cash_usd=money(snapshot.account.cash),
        available_cash_usd=money(snapshot.available_cash),
        reserved_cash_usd=money(snapshot.reserved_cash),
        fees_usd=money(snapshot.account.fees),
        realized_pnl_usd=money(snapshot.account.realized_pnl),
        positions=positions,
    )


def order(value: OrderSnapshot, version: int) -> Dict[str, Any]:
    return dict(
        order_id=value.order_id,
        symbol=value.symbol,
        side=value.side,
        quantity_shares=value.quantity,
        filled_shares=value.filled,
        remaining_shares=value.quantity - value.filled,
        limit_price_usd=money(value.limit_price),
        status=value.status,
        version=str(version),
    )


def bar(value: PriceVolumeBar, cutoff: int) -> Dict[str, Any]:
    return dict(
        start_ns=str(value.start_ns),
        end_ns=str(value.end_ns),
        observed_through_ns=str(cutoff),
        complete=value.end_ns <= cutoff,
        open_usd=money(value.open),
        high_usd=money(value.high),
        low_usd=money(value.low),
        close_usd=money(value.close),
        volume_shares=value.volume,
        trade_count=value.count,
        notional_usd=money(value.notional),
    )
