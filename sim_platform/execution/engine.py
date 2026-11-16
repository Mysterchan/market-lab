"""Pure displayed-limit transition shared by durable and in-memory playback."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Mapping, Tuple

from sim_platform.domain.models import AccountSnapshot, ContractError, OrderSnapshot

from .costs import charge_borrow, impacted_price
from .liquidity import consume, refresh
from .models import DISPLAYED_LIMIT_QUEUE, ExecutionConfig, Fill, Frontier
from .risk import OPEN, market_reason

if TYPE_CHECKING:  # Annotation only: execution does not import the market package at runtime.
    from sim_platform.market.models import MarketEvent, MarketSnapshot

# ITCH messages that change the displayed book: Add (A, F), Executed (E, C),
# Cancel (X), Delete (D) and Replace (U). Only these can make an order fillable.
BOOK_CHANGES = ('A', 'F', 'E', 'C', 'X', 'D', 'U', 'Z')
# A requote replaces the whole displayed book with new quotes (market.models.REQUOTE).
# Recordings prepared from price bars use it at the start of every bar.
REQUOTE = 'Z'
# Executions of resting displayed orders: only these move a passive order's queue.
EXECUTIONS = ('E', 'C')


def snapshot_universe(market: Any) -> List[str]:
    """The symbols a market snapshot covers.

    A snapshot of several symbols (market.models.MarketView) carries ``books`` by
    symbol; any other snapshot covers its own symbol only.
    """
    books = getattr(market, 'books', None)
    return list(books) if books else [market.symbol]


def snapshot_fields(market: Any) -> Dict[str, Any]:
    """A snapshot's own fields as a dict, without a MarketView's map of other books."""
    return {key: value for key, value in vars(market).items() if not key.startswith('_')}


def start_books(market: Any) -> Dict[str, Dict[str, Any]]:
    """Each symbol's book and fresh supply budget, for a run on several symbols."""
    return {
        symbol: dict(
            market=vars(book).copy(), bids=refresh([], book.bids), asks=refresh([], book.asks)
        )
        for symbol, book in market.books.items()
    }


def _marketable(order: OrderSnapshot, market: MarketSnapshot) -> bool:
    """Whether the order's limit reaches the opposite best displayed price."""
    if order.side == 'buy':
        return bool(market.asks) and order.limit_price >= market.asks[0][0]
    return bool(market.bids) and order.limit_price <= market.bids[0][0]


def _level(levels: Any, price: int) -> Any:
    """Displayed shares at ``price`` in a side's (price, shares) levels, or None."""
    for level_price, size in levels:
        if level_price == price:
            return size
    return None


def _queue_fills(
    config: ExecutionConfig,
    orders: List[OrderSnapshot],
    queues: Dict[str, int],
    previous: Mapping[str, Any],
    market: MarketSnapshot,
    executed: bool,
) -> List[Tuple[OrderSnapshot, int]]:
    """Fill resting orders from executions at their own price (queue model).

    An order joins the back of its price level: the shares displayed there when it is
    first seen are ahead of it. Executions at that level (a fall in displayed size on an
    E or C message) consume the shares ahead first, then fill the order. Cancellations
    are assumed to come from behind the order, so they never move it forward. A level
    outside the recorded depth cannot be observed, so its orders wait. ``queues`` is
    updated in place with the shares still ahead of each order.
    """
    fills = []
    for order in orders:
        side = 'bids' if order.side == 'buy' else 'asks'
        before = _level(previous.get(side) or (), order.limit_price)
        after = _level(getattr(market, side), order.limit_price) or 0
        if order.order_id not in queues:
            if before is None:
                continue
            queues[order.order_id] = before
        traded = before - after if executed and before is not None and after < before else 0
        if not traded:
            continue
        ahead = queues[order.order_id] - traded
        queues[order.order_id] = max(0, ahead)
        if ahead < 0:
            fills.append((order, min(order.quantity - order.filled, -ahead)))
    return fills


def _fill(
    config: ExecutionConfig,
    order: OrderSnapshot,
    accepted: Mapping[str, Any],
    market: MarketSnapshot,
    tick: int,
    quantity: int,
    price: int,
    **extra: Any,
) -> Fill:
    """One fill record; optional keys appear only when used, keeping older records' form."""
    fill = dict(
        order_id=order.order_id,
        quantity=quantity,
        price=price,
        fee=quantity * config.fee_per_share,
        model=config.model,
        market_sequence=market.sequence,
        tick=tick,
        **extra,
    )
    if accepted[order.order_id].data.get('short_selling'):
        # Only orders of runs that allow shorts carry this flag.
        fill['allow_short'] = True
    return fill


def advance(
    config: ExecutionConfig,
    account: AccountSnapshot,
    accepted: Mapping[str, Any],
    last: Frontier,
    event: MarketEvent,
) -> Tuple[Frontier, Tuple[Fill, ...]]:
    """Apply one market event to the financial frontier and return any fills.

    The function is pure: it reads the account and the previous frontier and returns
    a new frontier. The caller records the fills and updates the account ledger.

    An open order can fill only when the event changes the displayed book, the order
    was accepted on an earlier market sequence, its latency has elapsed, and its limit
    reaches the opposite best price. Orders fill in acceptance order against the
    supply budget, so two simulated orders never take the same displayed shares.

    Args:
        config: Fee and execution model for this run.
        account: The account as of the previous frontier, including open orders.
        accepted: Acceptance record for each open order ID. ``data`` holds
            ``submitted_sequence`` and ``eligible_ns``; ``sequence`` orders priority.
        last: The frontier returned for the previous event.
        event: The next committed market, clock or news event.

    Returns:
        The new frontier, and the fills produced by this event.

    Raises:
        ContractError: If time moves backwards, the event kind is not a committed
            market/publication event, or a market sequence number is skipped.
    """
    market = event.snapshot
    if market.time_ns < last.get('market', {}).get('time_ns', 0):
        raise ContractError('execution time moved backwards')
    if event.kind not in ('market', 'clock', 'news'):
        raise ContractError('expected a committed market or publication event')
    # News does not consume a market sequence number; every other event advances it by one.
    if market.sequence != last['market_sequence'] + (event.kind != 'news'):
        raise ContractError('execution market sequence gap')
    can_match = (
        event.kind == 'market'
        and event.source_type in BOOK_CHANGES
        and market.book_time_ns == market.time_ns
    )
    books = last.get('books')
    # Several symbols: this event changes only its own symbol's book and budget.
    own = books.get(market.symbol) if books is not None else last
    bids, asks = own['bids'], own['asks']
    if can_match:
        previous = own['market']
        if event.source_type == REQUOTE:
            # New quotes are new supply. Nothing consumed or remembered from the old
            # book carries over, which also keeps the budget from growing without bound.
            bids, asks = refresh([], market.bids), refresh([], market.asks)
        else:
            if market.bids != previous['bids']:
                bids = refresh(bids, market.bids)
            if market.asks != previous['asks']:
                asks = refresh(asks, market.asks)
    tick = last['tick'] + 1
    fills: List[Fill] = []
    queues = None
    if config.model == DISPLAYED_LIMIT_QUEUE:
        # Shares ahead of each open resting order; closed orders drop out.
        open_ids = {o.order_id for o in account.orders if o.status in OPEN}
        queues = {k: v for k, v in (last.get('queues') or {}).items() if k in open_ids}
    if can_match and market_reason(market) is None:

        def eligible(order: OrderSnapshot) -> bool:
            """Accepted before this market sequence, and past its simulated latency."""
            metadata = accepted[order.order_id].data
            return (
                market.sequence > metadata['submitted_sequence']
                and market.time_ns >= metadata['eligible_ns']
            )

        waiting = sorted(
            (
                o
                for o in account.orders
                if o.status in OPEN and o.symbol == market.symbol and eligible(o)
            ),
            key=lambda o: accepted[o.order_id].sequence,
        )
        orders = [o for o in waiting if _marketable(o, market)]
        # Copy only a budget that will be consumed; idle frames never copy account history.
        if orders:
            bids, asks = [list(x) for x in bids], [list(x) for x in asks]
        for order in orders:
            budget = asks if order.side == 'buy' else bids
            visible = {p for p, _ in (market.asks if order.side == 'buy' else market.bids)}
            opposite = market.asks if order.side == 'buy' else market.bids
            for price, quantity in consume(
                budget, order.side, order.limit_price, order.quantity - order.filled, visible
            ):
                paid = impacted_price(
                    config, order.side, price, quantity, opposite, order.limit_price
                )
                extra = dict(quoted_price=price) if paid != price else {}
                fills.append(_fill(config, order, accepted, market, tick, quantity, paid, **extra))
        if queues is not None:
            resting = [o for o in waiting if not _marketable(o, market)]
            executed = event.source_type in EXECUTIONS
            for order, quantity in _queue_fills(
                config, resting, queues, own['market'], market, executed
            ):
                fills.append(
                    _fill(
                        config,
                        order,
                        accepted,
                        market,
                        tick,
                        quantity,
                        order.limit_price,
                        liquidity='passive',
                    )
                )
    # MarketSnapshot is frozen and contains only scalars and immutable tuples.
    # A fresh outer mapping isolates the working state; recursively copying all
    # depth levels on every source event adds no isolation and stalls bursts.
    state = dict(
        tick=tick,
        market_sequence=market.sequence,
        event_kind=event.kind,
        source_type=event.source_type,
        market=snapshot_fields(market),
        bids=bids,
        asks=asks,
        ordinal=last.get('ordinal', 0),
        boundary=last.get('boundary', 0) + 1,
    )
    if queues is not None:
        state['queues'] = queues
    if config.borrow_bps_per_year:
        shorts = dict(last.get('shorts') or {})
        if fills:
            charge_borrow(
                config,
                {p.symbol: (p.quantity, p.cost) for p in account.positions},
                {o.order_id: (o.side, o.symbol) for o in account.orders},
                shorts,
                fills,
                market.time_ns,
            )
        state['shorts'] = shorts
    if books is not None:
        state['books'] = dict(
            books, **{market.symbol: dict(market=state['market'], bids=bids, asks=asks)}
        )
    return state, tuple(fills)
