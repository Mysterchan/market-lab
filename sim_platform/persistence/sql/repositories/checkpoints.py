"""Derived account checkpoint and indexed intent/order projections."""

from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING, Any, Dict, Optional

from sqlalchemy import select, func, delete
from sqlalchemy.orm import Session
from sim_platform.domain.models import ContractError, Intent, LedgerEvent, OrderSnapshot
from sim_platform.domain.json_values import copy_json
from sim_platform.domain.ledger import replay, apply_event
from sim_platform.domain.ledger.state import LedgerState, RecordedIntent
from sim_platform.domain.ledger.checkpoint import encode_state, decode_state
from sim_platform.persistence.sql.models import (
    AccountCheckpointRecord,
    LedgerEventRecord,
    LedgerItemRecord,
)
from sim_platform.persistence.sql.mapping import to_domain as _domain
from .events import EventRepository

if TYPE_CHECKING:  # Annotation only.
    from sqlalchemy import Select

# Version of checkpoint headers whose intents/orders live in ledger_items rows.
ITEM_PROJECTION_VERSION = 2


class CheckpointRepository:
    """Cached account ledger states, with their checkpoint header and item projections.

    Repositories creates one per Session. get() loads (or replays) an account's
    state once per Session; save() writes the header and changed item rows.
    """

    def __init__(self, session: Session, events: EventRepository) -> None:
        self.session, self.events = session, events
        self._states: Dict[str, LedgerState] = {}
        self._headers: Dict[str, Optional[AccountCheckpointRecord]] = {}

    def get(self, account_id: str) -> LedgerState:
        """The account's current ledger state, from its checkpoint plus newer events."""
        if account_id not in self._states:
            row = self.session.get(AccountCheckpointRecord, account_id)
            self._headers[account_id] = row
            state = decode_state(row.state) if row else replay(self.events.for_account(account_id))
            if row and row.state.get('version') == ITEM_PROJECTION_VERSION:
                query: Select[Any] = select(LedgerItemRecord).where(
                    LedgerItemRecord.account_id == account_id,
                    LedgerItemRecord.status.in_(('submitted', 'accepted', 'partial')),
                )
                for item in self.session.scalars(query):
                    self._load_item(state, item)
            if row:
                latest = (
                    self.session.scalar(
                        select(func.max(LedgerEventRecord.sequence)).where(
                            LedgerEventRecord.account_id == account_id
                        )
                    )
                    or 0
                )
                if state.account_id != account_id or state.sequence > latest:
                    raise ContractError('account checkpoint does not match its ledger')
                if row.state.get('version') == ITEM_PROJECTION_VERSION and state.sequence < latest:
                    # Item projections reflect the latest commit, not a deliberately stale header.
                    state = replay(self.events.for_account(account_id))
                query = (
                    select(LedgerEventRecord)
                    .where(
                        LedgerEventRecord.account_id == account_id,
                        LedgerEventRecord.sequence > state.sequence,
                    )
                    .order_by(LedgerEventRecord.sequence)
                )
                for event in self.session.scalars(query):
                    value = _domain(event, LedgerEvent)
                    self.prepare_event(state, value)
                    apply_event(state, value)
            self._states[account_id] = state
        return self._states[account_id]

    @staticmethod
    def _load_item(state: LedgerState, row: LedgerItemRecord) -> None:
        data = copy_json(row.data)
        if row.kind == 'order':
            state.orders[row.item_id] = OrderSnapshot(**data)
        else:
            data['intent'] = Intent(**data['intent'])
            state.intents[row.item_id] = RecordedIntent(**data)

    def prepare_event(self, state: LedgerState, event: LedgerEvent) -> None:
        """Load the intent/order rows an event refers to before it is applied."""
        for kind, key, values in (
            ('intent', 'intent_id', state.intents),
            ('order', 'order_id', state.orders),
        ):
            identity = event.data.get(key)
            if identity and identity not in values:
                row = self.session.get(LedgerItemRecord, (event.account_id, kind, identity))
                if row is not None:
                    self._load_item(state, row)

    def orders(self, account_id: str) -> Dict[str, OrderSnapshot]:
        """Every order of the account, including closed ones kept only in item rows."""
        state = self.get(account_id)
        values = {
            row.item_id: OrderSnapshot(**row.data)
            for row in self.session.scalars(
                select(LedgerItemRecord).where(
                    LedgerItemRecord.account_id == account_id, LedgerItemRecord.kind == 'order'
                )
            )
        }
        values.update(state.orders)
        return values

    def save(self, account_id: str) -> None:
        state = self.get(account_id)
        active_intents = {
            o.intent_id for o in state.orders.values() if o.status in ('accepted', 'partial')
        }
        for kind, items in (('intent', state.intents), ('order', state.orders)):
            for identity, item in items.items():
                data = asdict(item)
                status = item.status
                if kind == 'intent' and status == 'accepted' and identity not in active_intents:
                    status = 'resolved'
                row = self.session.get(LedgerItemRecord, (account_id, kind, identity))
                if row is None:
                    self.session.add(
                        LedgerItemRecord(
                            account_id=account_id,
                            kind=kind,
                            item_id=identity,
                            status=status,
                            data=data,
                        )
                    )
                elif row.status != status or row.data != data:
                    row.status, row.data = status, data
        value = encode_state(state)
        value.update(version=ITEM_PROJECTION_VERSION, intents={}, orders={})
        header = self._headers.get(account_id)
        if header is None:
            header = AccountCheckpointRecord(account_id=account_id, state=value)
            self._headers[account_id] = header
            self.session.add(header)
        else:
            header.state = value
        self.session.flush()

    def rebuild(self, account_id: str) -> None:
        """Replace the account's projections with a full replay of its ledger."""
        self._headers[account_id] = self.session.get(AccountCheckpointRecord, account_id)
        self.session.execute(
            delete(LedgerItemRecord).where(LedgerItemRecord.account_id == account_id)
        )
        self._states[account_id] = replay(self.events.for_account(account_id))
        self.save(account_id)
