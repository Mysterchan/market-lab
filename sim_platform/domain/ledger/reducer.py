"""Account-event orchestration: validate order, dispatch, then advance history.

Accounting operations live in actions.py; typed state lives in state.py.
The reducer has no database or ORM dependencies.
"""

from collections.abc import Iterable

from sim_platform.domain.ledger.actions import (
    accept_order,
    open_account,
    record_fill,
    reject_intent,
    submit_intent,
    cancel_order,
    execution_audit,
)
from sim_platform.domain.ledger.state import LedgerState
from sim_platform.domain.models import ContractError, LedgerEvent


_HANDLERS = {
    "opened": open_account,
    "intent": submit_intent,
    "accepted": accept_order,
    "rejected": reject_intent,
    "fill": record_fill,
    "cancelled": cancel_order,
    "execution_tick": execution_audit,
    "execution_end": execution_audit,
}


def empty_state() -> LedgerState:
    return LedgerState()


def _validate_history(state: LedgerState, event: LedgerEvent) -> None:
    if event.sequence != state.sequence + 1:
        raise ContractError("ledger sequence gap")
    if state.account_id is not None and event.account_id != state.account_id:
        raise ContractError("event belongs to another account")
    if event.event_id in state.event_ids:
        raise ContractError("duplicate ledger event ID")
    if state.sequence == 0 and event.kind != "opened":
        raise ContractError("missing account opening")
    # LedgerEvent guarantees an integer time for every event inside a run.
    if (
        event.run_id is not None
        and event.time_ns is not None
        and event.time_ns < state.run_times.get(event.run_id, 0)
    ):
        raise ContractError("simulation time moved backwards")


def _advance_history(state: LedgerState, event: LedgerEvent) -> None:
    state.account_id = event.account_id
    state.sequence = event.sequence
    state.event_ids.add(event.event_id)
    if event.run_id is not None and event.time_ns is not None:
        state.run_times[event.run_id] = event.time_ns


def apply_event(state: LedgerState, event: LedgerEvent) -> None:
    """Apply one event; a rejected event leaves accounting and history unchanged."""
    _validate_history(state, event)
    try:
        _HANDLERS[event.kind](state, event)
    except (KeyError, TypeError) as exc:
        raise ContractError(f"invalid {event.kind} event payload") from exc
    _advance_history(state, event)


def replay(events: Iterable[LedgerEvent]) -> LedgerState:
    state = empty_state()
    for event in events:
        apply_event(state, event)
    return state
