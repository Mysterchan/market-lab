"""One handler per accounting operation; validate, calculate, then commit."""

from dataclasses import replace

from sim_platform.domain.ledger.state import Fill, FillResult, LedgerState, RecordedIntent
from sim_platform.domain.models import (
    ContractError,
    Intent,
    LedgerEvent,
    OrderSnapshot,
    Position,
    integer,
    label,
)


def open_account(state: LedgerState, event: LedgerEvent) -> None:
    if state.sequence != 0:
        raise ContractError("account already opened")
    cash = event.data["cash"]
    integer(cash, "initial cash")
    state.cash = cash


def submit_intent(state: LedgerState, event: LedgerEvent) -> None:
    data = event.data
    intent = Intent(**data["intent"])
    intent.validate()
    intent_id, run_id, maker_id = data["intent_id"], data["run_id"], data["maker_id"]
    label(intent_id, "intent ID", 1024)
    label(maker_id, "maker ID")
    if run_id != event.run_id:
        raise ContractError("intent belongs to another run")
    if intent_id in state.intents:
        raise ContractError("duplicate intent")
    state.intents[intent_id] = RecordedIntent(intent_id, run_id, maker_id, intent)


def _submitted_intent(state: LedgerState, event: LedgerEvent) -> RecordedIntent:
    intent = state.intents.get(event.data["intent_id"])
    if intent is None or intent.status != "submitted":
        raise ContractError("intent is unknown or already resolved")
    if intent.run_id != event.run_id:
        raise ContractError("intent belongs to another run")
    return intent


def accept_order(state: LedgerState, event: LedgerEvent) -> None:
    recorded = _submitted_intent(state, event)
    order_id = event.data["order_id"]
    label(order_id, "order ID")
    if order_id in state.orders:
        raise ContractError("duplicate order")
    intent = recorded.intent
    order = OrderSnapshot(
        order_id,
        recorded.intent_id,
        recorded.run_id,
        intent.symbol,
        intent.side,
        intent.quantity,
        intent.limit_price,
        0,
        "accepted",
    )
    accepted = replace(recorded, status="accepted")
    state.orders[order_id] = order
    state.intents[recorded.intent_id] = accepted


def reject_intent(state: LedgerState, event: LedgerEvent) -> None:
    recorded = _submitted_intent(state, event)
    label(event.data["reason"], "rejection reason")
    state.intents[recorded.intent_id] = replace(recorded, status="rejected")


def _validate_fill_order(state: LedgerState, event: LedgerEvent, fill: Fill) -> OrderSnapshot:
    fill.validate()
    order = state.orders.get(fill.order_id)
    if order is None or order.run_id != event.run_id:
        raise ContractError("unknown order or wrong run")
    if order.status not in ("accepted", "partial"):
        raise ContractError("order is not open")
    if fill.quantity > order.quantity - order.filled:
        raise ContractError("fill exceeds remaining order quantity")
    violates_limit = (order.side == "buy" and fill.price > order.limit_price) or (
        order.side == "sell" and fill.price < order.limit_price
    )
    if violates_limit:
        raise ContractError("fill violates limit price")
    return order


def _filled_order(order: OrderSnapshot, fill: Fill) -> OrderSnapshot:
    filled = order.filled + fill.quantity
    status = "filled" if filled == order.quantity else "partial"
    return replace(order, filled=filled, status=status)


def _calculate_buy(state: LedgerState, order: OrderSnapshot, fill: Fill) -> FillResult:
    amount = fill.notional + fill.fee
    if amount > state.cash:
        raise ContractError("insufficient cash")
    previous = state.positions.get(order.symbol, Position(order.symbol, 0, 0))
    if previous.quantity < 0:
        return _cover_short(state, order, fill, previous)
    position = Position(order.symbol, previous.quantity + fill.quantity, previous.cost + amount)
    return FillResult(
        state.cash - amount,
        position,
        state.fees + fill.fee,
        state.realized_pnl,
        _filled_order(order, fill),
    )


def _cover_short(
    state: LedgerState, order: OrderSnapshot, fill: Fill, previous: Position
) -> FillResult:
    """A buy against a short: cover first, then hold any remaining shares long.

    A short's cost is its proceeds after fees, as a negative number. Covering part of
    it releases the same share of those proceeds, rounded toward zero, or all of them
    when the short closes; realized P&L is the released proceeds minus the cover's
    value and fee (docs/reference/orders.md).
    """
    short = -previous.quantity
    covering = min(fill.quantity, short)
    fee_cover = fill.fee * covering // fill.quantity
    released = -previous.cost if covering == short else -previous.cost * covering // short
    realized = released - (covering * fill.price + fee_cover)
    cost = previous.cost + released
    long_part = fill.quantity - covering
    if long_part:
        cost += long_part * fill.price + (fill.fee - fee_cover)
    return FillResult(
        state.cash - fill.notional - fill.fee,
        Position(order.symbol, previous.quantity + fill.quantity, cost),
        state.fees + fill.fee,
        state.realized_pnl + realized,
        _filled_order(order, fill),
    )


def _allocated_cost(position: Position, quantity: int) -> int:
    """Floor partial-sale cost; a final sale consumes all remaining cost units."""
    if quantity == position.quantity:
        return position.cost
    return position.cost * quantity // position.quantity


def _sell_short(
    state: LedgerState, order: OrderSnapshot, fill: Fill, previous: Position
) -> FillResult:
    """A sale beyond the shares held: close any long part, open a short with the rest.

    The short's cost becomes its proceeds after its share of the fee, as a negative
    number; the long part, if any, realizes P&L as an ordinary sale.
    """
    closing = max(0, previous.quantity)
    fee_close = fill.fee * closing // fill.quantity
    realized = closing * fill.price - fee_close - previous.cost if closing else 0
    opening = fill.quantity - closing
    base = 0 if closing else previous.cost
    cost = base - (opening * fill.price - (fill.fee - fee_close))
    return FillResult(
        state.cash + fill.notional - fill.fee,
        Position(order.symbol, previous.quantity - fill.quantity, cost),
        state.fees + fill.fee,
        state.realized_pnl + realized,
        _filled_order(order, fill),
    )


def _calculate_sell(
    state: LedgerState, order: OrderSnapshot, fill: Fill, allow_short: bool = False
) -> FillResult:
    previous = state.positions.get(order.symbol, Position(order.symbol, 0, 0))
    if fill.quantity > previous.quantity:
        if not allow_short:
            raise ContractError("short selling is unsupported")
        return _sell_short(state, order, fill, previous)
    allocated_cost = _allocated_cost(previous, fill.quantity)
    proceeds = fill.notional - fill.fee
    cash = state.cash + proceeds
    if cash < 0:
        raise ContractError("insufficient cash for sell fee")
    position = Position(
        order.symbol, previous.quantity - fill.quantity, previous.cost - allocated_cost
    )
    realized_pnl = state.realized_pnl + proceeds - allocated_cost
    return FillResult(
        cash, position, state.fees + fill.fee, realized_pnl, _filled_order(order, fill)
    )


def _commit_fill(state: LedgerState, result: FillResult) -> None:
    """Only assignments remain after the entire proposed update is validated."""
    state.cash = result.cash
    state.fees = result.fees
    state.realized_pnl = result.realized_pnl
    state.positions[result.position.symbol] = result.position
    state.orders[result.order.order_id] = result.order


def record_fill(state: LedgerState, event: LedgerEvent) -> None:
    data = event.data
    fill = Fill(data["order_id"], data["quantity"], data["price"], data["fee"])
    order = _validate_fill_order(state, event, fill)
    if order.side == "buy":
        result = _calculate_buy(state, order, fill)
    else:
        # Present only on fills of runs that allow short selling.
        allow_short = data.get("allow_short", False) is True
        result = _calculate_sell(state, order, fill, allow_short)
    result.validate()
    _commit_fill(state, result)


def cancel_order(state: LedgerState, event: LedgerEvent) -> None:
    order = state.orders.get(event.data["order_id"])
    if order is None or order.run_id != event.run_id:
        raise ContractError("unknown order or wrong run")
    if order.status not in ("accepted", "partial"):
        raise ContractError("order is not open")
    label(event.data["reason"], "cancellation reason")
    state.orders[order.order_id] = replace(order, status="cancelled")


def execution_audit(state: LedgerState, event: LedgerEvent) -> None:
    # Audit events advance history/time without modifying cash or positions.
    if event.kind == "execution_tick":
        integer(event.data["market_sequence"], "market sequence")
        integer(event.data["tick"], "execution tick", 1)
    else:
        label(event.data["reason"], "execution end reason")
