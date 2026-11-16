"""ResearchQueries operations within one caller-owned transaction."""

from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING, Any, Dict, Iterator, Optional, Tuple

from sim_platform.domain.models import ContractError

if TYPE_CHECKING:  # Annotation only.
    from .accounts import AccountService
    from .runs import RunService

# The unit of work's persistence Repositories; use cases may not import persistence.
Repositories = Any

# Most recent events and decisions in a report view.
VIEW_RECORD_LIMIT = 100

# Report data (see research.reports.ReportData).
ReportData = Dict[str, Any]


class ResearchQueries:
    """Report and archive reads for research, inside the caller's unit of work."""

    def __init__(
        self, repositories: Repositories, accounts: AccountService, runs: RunService
    ) -> None:
        self.repo, self.accounts, self.runs = repositories, accounts, runs

    def archive_market(self, profile_id: str, run_id: str, archive: Any) -> None:
        run = self.runs.owned_run(profile_id, run_id)
        if not isinstance(run.config, dict) or (
            'decision' not in run.config and not run.config.get('agent_experiment_id')
        ):
            raise ContractError('market archive requires a decision or bound agent run')
        if run.config.get('agent_experiment_id') and run.status == 'running':
            raise ContractError('archive agent market only after its owner stops')
        self.repo.runs.save_archive(run_id, archive)

    def report_data(self, profile_id: str, run_id: str) -> ReportData:
        """Read the run and its historical account prefix in one transaction."""
        run = self.runs.run_details(profile_id, run_id)
        own = [asdict(e) for e in self.repo.events.for_run(run_id)]
        end = max((e['sequence'] for e in own), default=run['initial_snapshot']['sequence'])
        prefix = [asdict(e) for e in self.repo.events.for_account(run['account_id'], end)]
        return dict(
            run=run,
            last_market=self._last_market(run_id),
            account_ledger=prefix,
            run_events=own,
            final=asdict(self.accounts.replay_snapshot(profile_id, run['account_id'], prefix)),
        )

    def report_view_data(self, profile_id: str, run_id: str) -> ReportData:
        """Bounded historical view; old runs without a final projection fall back to ledger reconstruction."""
        run = self.runs.run_details(profile_id, run_id, include_records=False)
        final = self.repo.runs.final(run_id)
        if final is None:
            if run['status'] == 'running':
                final = asdict(self.accounts.snapshot(profile_id, run['account_id']))
            else:
                final = self.report_data(profile_id, run_id)['final']
        own = [asdict(e) for e in self.repo.events.for_run(run_id, limit=VIEW_RECORD_LIMIT)]
        return dict(
            run=run,
            final=final,
            run_events=own,
            account_ledger=[],
            last_market=self._last_market(run_id),
            fill_count=self.repo.events.count(run_id, 'fill'),
            decision_count=run['policy_state'].get('_record_count', 0),
            decision_tail=self.repo.runs.records(run_id, limit=VIEW_RECORD_LIMIT),
        )

    def _last_market(self, run_id: str) -> Optional[Dict[str, Any]]:
        """The last market snapshot from the checkpoint, archive or last execution tick."""
        state = self.repo.runs.execution_state(run_id)
        if state is not None:
            return state['market']
        archive = self.repo.runs.archive(run_id)
        if archive is not None:
            if 'execution' in archive:
                return archive['execution']['market']
            market = archive.get('market', {}).get('snapshot')
            if market is not None:
                return market
        tick = self.repo.events.latest(run_id, 'execution_tick')
        return tick.data['market'] if tick else None

    def report_stream_data(
        self, profile_id: str, run_id: str
    ) -> Tuple[ReportData, Dict[str, Iterator[Dict[str, Any]]]]:
        """The report view plus iterators over the full ledger, events and decisions."""
        data = self.report_view_data(profile_id, run_id)
        tail = self.repo.events.for_run(run_id, limit=1)
        end = tail[0].sequence if tail else data['run']['initial_snapshot']['sequence']
        return data, dict(
            account_ledger=self.repo.events.iter_account(data['run']['account_id'], end),
            run_events=self.repo.events.iter_run(run_id),
            decisions=self.repo.runs.iter_records(run_id),
            nested_decisions=self.repo.runs.iter_records(run_id),
        )
