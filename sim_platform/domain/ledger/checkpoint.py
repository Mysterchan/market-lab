"""Versioned accounting checkpoint; the complete event ledger remains authoritative.

Event IDs are checked against the ledger's unique index by the application before
appending. They are deliberately not copied into a growing checkpoint on each tick.
Full replay retains all history checks, including duplicate IDs.
"""

from dataclasses import asdict

from .state import LedgerState, RecordedIntent
from sim_platform.domain.models import ContractError, Intent, Position, OrderSnapshot


def encode_state(state: LedgerState) -> dict:
    return dict(
        version=1,
        cash=state.cash,
        fees=state.fees,
        realized_pnl=state.realized_pnl,
        sequence=state.sequence,
        account_id=state.account_id,
        positions={k: asdict(v) for k, v in state.positions.items()},
        intents={k: asdict(v) for k, v in state.intents.items()},
        orders={k: asdict(v) for k, v in state.orders.items()},
        run_times=state.run_times.copy(),
    )


def decode_state(value: dict) -> LedgerState:
    if value.get('version') not in (1, 2):
        raise ContractError('unsupported account checkpoint version')
    return LedgerState(
        cash=value['cash'],
        fees=value['fees'],
        realized_pnl=value['realized_pnl'],
        sequence=value['sequence'],
        account_id=value['account_id'],
        positions={k: Position(**v) for k, v in value['positions'].items()},
        intents={
            k: RecordedIntent(**{**v, 'intent': Intent(**v['intent'])})
            for k, v in value['intents'].items()
        },
        orders={k: OrderSnapshot(**v) for k, v in value['orders'].items()},
        run_times=value['run_times'].copy(),
    )
