"""Reservations and acceptance checks, separate from fills/accounting."""

from __future__ import annotations

from typing import TYPE_CHECKING, Dict, Optional, Tuple

from sim_platform.domain.models import AccountSnapshot, ContractError, Intent, integer

from .models import ExecutionConfig

if TYPE_CHECKING:  # Annotation only: execution does not import the market package at runtime.
    from sim_platform.market.models import MarketSnapshot

# Order states that can still fill, and therefore still hold cash or shares.
OPEN = ('accepted', 'partial')
# A market order's protection: how far past the best opposite price it may fill, in
# basis points of that price (docs/reference/orders.md).
MARKET_PROTECTION_BPS = 500


def protection_price(side: str, market: MarketSnapshot) -> Optional[int]:
    """The limit a market order is accepted at, or None without an opposite quote.

    A buy may pay up to the best ask plus 5%, rounded up; a sell may receive down to
    the best bid minus 5%, rounded down and at least one money unit.
    """
    if side == 'buy':
        if not market.asks:
            return None
        ask = market.asks[0][0]
        return ask + -(-ask * MARKET_PROTECTION_BPS // 10_000)
    if not market.bids:
        return None
    bid = market.bids[0][0]
    return max(1, bid - bid * MARKET_PROTECTION_BPS // 10_000)


# The short-selling rule recorded with a run that allows shorts (docs/reference/orders.md):
# opening a short reserves 50% of its value; a filled short holds 150% of its proceeds.
SHORT_SELLING_RULE = dict(rule='reg-t-initial-margin', initial_margin_bps=5000, hold_bps=15000)


def short_hold(account: AccountSnapshot, rule: Optional[Dict[str, int]]) -> int:
    """Cash held back for filled short positions: hold_bps of their proceeds."""
    if not rule:
        return 0
    proceeds = sum(-p.cost for p in account.positions if p.quantity < 0)
    return proceeds * rule['hold_bps'] // 10_000


def short_margin(quantity: int, price: int, fee: int, rule: Dict[str, int]) -> int:
    """Initial margin for opening ``quantity`` shares short at ``price``, plus their fees."""
    return -(-quantity * price * rule['initial_margin_bps'] // 10_000) + quantity * fee


def reservations(
    account: AccountSnapshot, config: ExecutionConfig, short: Optional[Dict[str, int]] = None
) -> Tuple[int, Dict[str, int]]:
    """Return what the account's open orders already hold.

    Open buys hold cash for their unfilled quantity at the limit price plus fees.
    Open sells hold their unfilled shares. Both are unavailable to new orders. With
    ``short`` (the run's short-selling rule), the part of open sells beyond the shares
    held instead holds its initial margin in cash, and filled shorts hold cash too.

    Returns:
        Reserved cash, and reserved shares by symbol.
    """
    cash = short_hold(account, short)
    shares: Dict[str, int] = {}
    owned = {p.symbol: max(0, p.quantity) for p in account.positions}
    for order in account.orders:
        if order.status not in OPEN:
            continue
        remaining = order.quantity - order.filled
        if order.side == 'buy':
            cash += remaining * (order.limit_price + config.fee_per_share)
        elif short is None:
            shares[order.symbol] = shares.get(order.symbol, 0) + remaining
        else:
            free = max(0, owned.get(order.symbol, 0) - shares.get(order.symbol, 0))
            covered = min(remaining, free)
            shares[order.symbol] = shares.get(order.symbol, 0) + covered
            if remaining > covered:
                cash += short_margin(
                    remaining - covered, order.limit_price, config.fee_per_share, short
                )
    return cash, shares


def market_reason(market: MarketSnapshot) -> Optional[str]:
    """Explain why this market state cannot accept or fill orders, or return None."""
    if market.status not in ('ready', 'paused', 'playing'):
        return 'market session is not active'
    if market.integrity not in ('ordered_unverified', 'sequenced'):
        return 'market feed is desynchronized or unavailable'
    if market.book_time_ns is None:
        return 'no order book observed yet'
    # ITCH trading states: Halted, Paused, Quotation only.
    if market.trading_state in ('H', 'P', 'Q'):
        return 'stock is halted or not released for trading'
    # ITCH system events: end of Market hours, End of system hours, end of messages (C).
    if market.system_event in ('M', 'E', 'C'):
        return 'market hours have ended'
    if market.bids and market.asks and market.bids[0][0] >= market.asks[0][0]:
        return 'crossed or locked book is unsupported'
    return None


def rejection_reason(
    intent: Intent,
    account: AccountSnapshot,
    config: ExecutionConfig,
    market: MarketSnapshot,
    short: Optional[Dict[str, int]] = None,
) -> Optional[str]:
    """Explain why an order must be rejected, or return None when it is acceptable.

    Checks run in a fixed order: market state, symbol, size limit, integer range,
    then funds for a buy or owned shares for a sell. Amounts already reserved by open
    orders are not available. Without ``short`` (the run's short-selling rule) a sale
    beyond the shares held is rejected; with it, that excess needs initial margin.
    """
    reason = market_reason(market)
    if reason:
        return reason
    if intent.symbol != market.symbol:
        return 'symbol is outside the market session'
    if intent.quantity > config.max_order_quantity:
        return 'order exceeds maximum quantity'
    cost = intent.quantity * (intent.limit_price + config.fee_per_share)
    try:
        integer(cost, 'reserved notional', 1)
        integer(market.time_ns + config.max_delay_ns, 'eligible time')
    except ContractError as exc:
        return str(exc)
    reserved_cash, reserved_shares = reservations(account, config, short)
    owned = next((p.quantity for p in account.positions if p.symbol == intent.symbol), 0)
    if intent.side == 'buy':
        # Covering a short releases its share of the cash held for it.
        released = (
            short_hold(account, short) * min(intent.quantity, -owned) // -owned
            if short and owned < 0
            else 0
        )
        if cost > account.cash - reserved_cash + released:
            return 'insufficient available cash including reservations and fees'
    else:
        free = max(0, owned) - reserved_shares.get(intent.symbol, 0)
        if intent.quantity > free:
            if short is None:
                return 'insufficient available shares; short selling is unsupported'
            margin = short_margin(
                intent.quantity - max(0, free), intent.limit_price, config.fee_per_share, short
            )
            if margin > account.cash - reserved_cash:
                return 'insufficient available cash for short-sale margin'
        if config.fee_per_share > intent.limit_price:
            return 'sell limit must cover per-share fee'
    return None
