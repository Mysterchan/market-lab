"""Transaction-bound repository composition and explicit class exports.

Read each named repository module for SQL queries. Repositories constructs them
with the caller's Session; it does not start or commit a transaction.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from .accounts import AccountRepository
from .agents import AgentRepository, AgentExperimentRepository
from .base import Repository
from .checkpoints import CheckpointRepository
from .events import EventRepository
from .makers import DecisionMakerRepository
from .profiles import ProfileRepository
from .runs import RunRepository


class Repositories:
    """Every repository bound to one Session; created per unit of work."""

    def __init__(self, session: Session) -> None:
        self.profiles = ProfileRepository(session)
        self.accounts = AccountRepository(session)
        self.makers = DecisionMakerRepository(session)
        self.runs = RunRepository(session)
        self.events = EventRepository(session)
        self.checkpoints = CheckpointRepository(session, self.events)
        self.agents = AgentRepository(session)
        self.agent_experiments = AgentExperimentRepository(session)
