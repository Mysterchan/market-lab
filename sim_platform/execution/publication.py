"""Read committed transaction values into the owner's bounded runtime view.

These functions run inside the existing unit of work. They do not write events,
change orders or publish RAM. ExecutionCoordinator installs the returned values
only after that transaction commits.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Dict, List, Optional

from sim_platform.domain.json_values import copy_json
from sim_platform.domain.models import AccountSnapshot, Run

from .models import ExecutionSnapshot, Frontier
from .risk import OPEN
from .state import RuntimeData

# History kept in the owner's read data; the Desktop shows the same number of entries.
HISTORY_LIMIT = 100


def accepted_orders(repo: Any, run_id: str, account: AccountSnapshot) -> Dict[str, Any]:
    """Read acceptance metadata for this run's currently open orders."""
    identifiers = [
        f'{order.intent_id}:accepted'
        for order in account.orders
        if order.run_id == run_id and order.status in OPEN
    ]
    return {
        event.data['order_id']: event
        for event in repo.events.by_ids(account.account_id, identifiers)
    }


def _run_details(
    accounts: Any, profile: str, run: Run, previous: Optional[RuntimeData]
) -> Dict[str, Any]:
    """Describe the run, rereading its journal and decision maker only when they changed."""
    if previous is None:
        return accounts.runs.run_details(profile, run.id, include_records=False)
    details = asdict(run)
    details['policy_state'] = (
        copy_json(run.policy_state)
        if run.status == 'running'
        else accounts.repo.runs.journal(run.id, include_records=False)
    )
    details['decision_maker'] = (
        previous.run['decision_maker']
        if run.maker_id == previous.run['maker_id']
        else asdict(accounts.repo.makers.get(run.maker_id))
    )
    return details


def _recent_events(
    repo: Any, run_id: str, account: AccountSnapshot, previous: Optional[RuntimeData]
) -> List[Dict[str, Any]]:
    """Return the latest run events, appending only those added since ``previous``."""
    if previous is None:
        return [asdict(event) for event in repo.events.for_run(run_id, limit=HISTORY_LIMIT)]
    if account.sequence == previous.account.sequence:
        return previous.events
    added = repo.events.after(run_id, previous.account.sequence)
    return (previous.events + [asdict(event) for event in added])[-HISTORY_LIMIT:]


def _recent_decisions(
    repo: Any, run_id: str, details: Dict[str, Any], previous: Optional[RuntimeData]
) -> List[Dict[str, Any]]:
    """Return the latest decision summaries, reading only records added since ``previous``."""
    if previous is None:
        return repo.runs.record_summaries(run_id)
    old_count = previous.run['policy_state'].get('_record_count', 0)
    new_count = details['policy_state'].get('_record_count', 0)
    if new_count <= old_count:
        return previous.decisions
    added = repo.runs.record_summaries(run_id, after_index=old_count)
    return (previous.decisions + added)[-HISTORY_LIMIT:]


def build_publication(
    accounts: Any,
    profile: str,
    run: Run,
    account: AccountSnapshot,
    last: Frontier,
    snapshot: ExecutionSnapshot,
    *,
    previous: Optional[RuntimeData] = None,
    account_changed: bool = False,
) -> RuntimeData:
    """Assemble initial or incremental data without reloading unchanged history.

    ``previous`` is the last committed view. Account changes require refreshing
    open-order metadata; decision counts determine which new summaries to read.
    Returned history is bounded to the same 100 entries as the owner's UI view.
    """
    repo = accounts.repo
    accepted = (
        previous.accepted
        if previous is not None and not account_changed
        else accepted_orders(repo, run.id, account)
    )
    details = _run_details(accounts, profile, run, previous)
    details['time_ns'] = run.time_ns
    return RuntimeData(
        profile,
        details,
        account,
        accepted,
        copy_json(last),
        snapshot,
        _recent_events(repo, run.id, account, previous),
        _recent_decisions(repo, run.id, details, previous),
    )
