"""Compose small use-case services inside a single unit of work."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sim_platform.application.use_cases.accounts import AccountService
from sim_platform.application.use_cases.makers import MakerService
from sim_platform.application.use_cases.runs import RunService
from sim_platform.application.use_cases.queries import ResearchQueries
from sim_platform.application.use_cases.agents import AgentService
from sim_platform.application.use_cases.agent_logs import AgentLogService
from sim_platform.application.use_cases.agent_runs import AgentRunService

if TYPE_CHECKING:  # Annotation only.
    from sim_platform.domain.models import AccountSnapshot, Run
    from sim_platform.persistence.sql.repositories import Repositories


class ApplicationServices:
    """Every use-case service bound to one unit of work's repositories.

    Created per transaction by DatabaseOperations (and by the execution service);
    it lives only as long as that transaction.
    """

    def __init__(self, repositories: Repositories) -> None:
        self.repo = repositories
        self.accounts = AccountService(repositories)
        self.makers = MakerService(repositories)
        self.runs = RunService(repositories, self.accounts, self.makers)
        self.research = ResearchQueries(repositories, self.accounts, self.runs)
        self.agents = AgentService(repositories, self.accounts)
        self.agent_logs = AgentLogService(repositories, self.runs)
        self.agent_runs = AgentRunService(repositories, self.accounts, self.runs, self.agents)

    def owned_run(self, profile: str, run_id: str) -> Run:
        return self.runs.owned_run(profile, run_id)

    def snapshot(self, profile: str, account_id: str, **options: Any) -> AccountSnapshot:
        return self.accounts.snapshot(profile, account_id, **options)

    def record_run_event(self, *args: Any) -> None:
        return self.runs.record_run_event(*args)

    def finish_run(self, *args: Any) -> None:
        return self.runs.finish_run(*args)
