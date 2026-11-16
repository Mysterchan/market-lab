"""AccountService operations within one caller-owned transaction."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Dict, Iterable, List, Optional

from sim_platform.domain.models import (
    AccountSnapshot,
    ContractError,
    LedgerEvent,
    Profile,
    SimulationAccount,
    integer,
    label,
)
from sim_platform.domain.json_values import copy_json, encode
from sim_platform.domain.ledger import apply_event, replay
from sim_platform.domain.ledger.state import LedgerState
from sim_platform.application.identifiers import identifier


# The unit of work's persistence Repositories; use cases may not import persistence.
Repositories = Any

# Longest ledger event ID.
EVENT_ID_MAX_LENGTH = 1024


class AccountService:
    """Profiles, accounts and ledger events inside the caller's unit of work."""

    def __init__(self, repositories: Repositories) -> None:
        self.repo = repositories

    def create_profile(self, name: str) -> str:
        label(name, "profile name")
        profile = Profile(identifier(), name)
        self.repo.profiles.add(profile)
        return profile.id

    def create_account(self, profile_id: str, name: str, cash: int, currency: str = "USD") -> str:
        """Create a USD account with an 'opened' ledger event for ``cash``; return its ID."""
        label(name, "account name")
        integer(cash, "initial cash")
        if currency != "USD":
            raise ContractError("Only USD accounts are supported")
        self.repo.profiles.get(profile_id)
        account = SimulationAccount(identifier(), profile_id, name, currency)
        self.repo.accounts.add(account)
        self.append_event(account.id, "opening", None, None, "opened", {"cash": cash})
        return account.id

    def rebuild_account(self, profile_id: str, account_id: str) -> AccountSnapshot:
        self.repo.accounts.owned(account_id, profile_id)
        self.repo.checkpoints.rebuild(account_id)
        return self.snapshot(profile_id, account_id)

    def event_dicts(self, account_id: str) -> List[Dict[str, Any]]:
        return [asdict(event) for event in self.repo.events.for_account(account_id)]

    def events(self, profile_id: str, account_id: str) -> List[Dict[str, Any]]:
        self.repo.accounts.owned(account_id, profile_id)
        return self.event_dicts(account_id)

    def snapshot(
        self, profile_id: str, account_id: str, *, include_closed: bool = True
    ) -> AccountSnapshot:
        account = self.repo.accounts.owned(account_id, profile_id)
        state = self.repo.checkpoints.get(account_id)
        if include_closed:
            from dataclasses import replace

            state = replace(state, orders=self.repo.checkpoints.orders(account_id))
        return self._snapshot(account, state)

    @staticmethod
    def _snapshot(account: SimulationAccount, state: LedgerState) -> AccountSnapshot:
        orders = tuple(order for _, order in sorted(state.orders.items()))
        positions = tuple(
            position for _, position in sorted(state.positions.items()) if position.quantity
        )
        return AccountSnapshot(
            account.id,
            account.profile_id,
            account.currency,
            state.cash,
            positions,
            state.fees,
            state.realized_pnl,
            state.sequence,
            orders,
        )

    def replay_snapshot(
        self, profile_id: str, account_id: str, events: Iterable[Dict[str, Any]]
    ) -> AccountSnapshot:
        """The account snapshot rebuilt from the given event records alone."""
        account = self.repo.accounts.owned(account_id, profile_id)
        parsed = [LedgerEvent.from_record(event) for event in events]
        if not parsed or any(event.account_id != account_id for event in parsed):
            raise ContractError("replay requires this account's nonempty event stream")
        return self._snapshot(account, replay(parsed))

    def append_event(
        self,
        account_id: str,
        event_id: str,
        run_id: Optional[str],
        time_ns: Optional[int],
        kind: str,
        data: Any,
    ) -> LedgerEvent:
        """Append a ledger event; a repeated ID with identical content returns the original."""
        label(event_id, "event ID", EVENT_ID_MAX_LENGTH)
        payload = copy_json(data)
        previous = self.repo.events.find(account_id, event_id)
        if previous:
            if (previous.run_id, previous.time_ns, previous.kind, encode(previous.data)) != (
                run_id,
                time_ns,
                kind,
                encode(payload),
            ):
                raise ContractError("event ID reused with different content")
            return previous
        state = self.repo.checkpoints.get(account_id)
        event = LedgerEvent(
            account_id, state.sequence + 1, event_id, run_id, time_ns, kind, payload
        )
        self.repo.checkpoints.prepare_event(state, event)
        apply_event(state, event)
        self.repo.events.add(event)
        self.repo.checkpoints.save(account_id)
        return event
