"""Compact stored order references; public decision records retain full observations.

References name an immutable ledger prefix, never the mutable ledger_items rows.
One reader advances through prefixes incrementally when exporting multiple records.
"""

from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256
from typing import Any, Dict, List

from sqlalchemy import select
from sqlalchemy.orm import Session

from sim_platform.domain.json_values import copy_json, encode
from sim_platform.domain.ledger.reducer import empty_state, apply_event
from sim_platform.domain.models import ContractError, LedgerEvent, integer
from sim_platform.persistence.sql.mapping import to_domain
from sim_platform.persistence.sql.models import LedgerEventRecord, RunRecord


# Version of the stored 'orders_ref' format.
ORDERS_REF_VERSION = 1
# Ledger rows fetched per batch while expanding a reference.
LEDGER_BATCH_ROWS = 256


def _digest(orders: List[Dict[str, Any]]) -> str:
    return sha256(encode(orders).encode()).hexdigest()


class DecisionRecordCodec:
    """Replaces a decision record's order list with a ledger reference, and back.

    Created per run inside one Session. expand() keeps the replayed ledger state
    between calls, so records read in increasing order replay each event once.
    """

    def __init__(self, session: Session, run_id: str) -> None:
        self.session = session
        run = session.get(RunRecord, run_id)
        if run is None:
            raise ContractError('unknown decision run')
        self.account_id = run.account_id
        self.state = empty_state()

    def compact(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """Copy of ``record`` with its orders replaced by a ledger-prefix reference."""
        value = copy_json(record)
        account = value['observation']['execution']['account']
        sequence = account['sequence']
        integer(sequence, 'decision ledger sequence', 1)
        if (
            account['account_id'] != self.account_id
            or self.session.get(LedgerEventRecord, (self.account_id, sequence)) is None
        ):
            raise ContractError('decision orders reference an unknown ledger prefix')
        if 'orders_ref' in account:
            raise ContractError('decision observation already contains an orders reference')
        orders = account.pop('orders')
        account['orders_ref'] = dict(
            version=ORDERS_REF_VERSION,
            account_id=self.account_id,
            sequence=sequence,
            sha256=_digest(orders),
        )
        return value

    def expand(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """Copy of ``record`` with referenced orders rebuilt and checked against the digest."""
        value = copy_json(record)
        account = value['observation']['execution']['account']
        if 'orders_ref' not in account:
            return value  # Existing full-snapshot records remain readable.
        reference = account.pop('orders_ref')
        if (
            not isinstance(reference, dict)
            or set(reference) != {'version', 'account_id', 'sequence', 'sha256'}
            or type(reference['version']) is not int
            or reference['version'] != ORDERS_REF_VERSION
            or reference['account_id'] != self.account_id
            or reference['account_id'] != account['account_id']
            or reference['sequence'] != account['sequence']
            or 'orders' in account
        ):
            raise ContractError('invalid decision orders reference')
        sequence = reference['sequence']
        integer(sequence, 'decision ledger sequence', 1)
        if sequence < self.state.sequence:
            self.state = empty_state()
        if sequence > self.state.sequence:
            query = (
                select(LedgerEventRecord)
                .where(
                    LedgerEventRecord.account_id == self.account_id,
                    LedgerEventRecord.sequence > self.state.sequence,
                    LedgerEventRecord.sequence <= sequence,
                )
                .order_by(LedgerEventRecord.sequence)
            )
            for row in self.session.scalars(query.execution_options(yield_per=LEDGER_BATCH_ROWS)):
                apply_event(self.state, to_domain(row, LedgerEvent))
        if self.state.sequence != sequence:
            raise ContractError('decision orders reference an incomplete ledger prefix')
        orders = [asdict(order) for _, order in sorted(self.state.orders.items())]
        if _digest(orders) != reference['sha256']:
            raise ContractError('decision orders do not match the recorded observation')
        account['orders'] = orders
        return value
