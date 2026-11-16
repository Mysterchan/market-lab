"""ORM entities and relationships. Tables are generated from these declarations."""

from __future__ import annotations

from typing import Any, List, Optional

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from sim_platform.domain.json_values import encode
import json


class CanonicalJSON(TypeDecorator):
    """Keep v1 TEXT bytes compatible while mapping stored JSON to Python values."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Dialect) -> str:
        return encode(value)

    def process_result_value(self, value: Any, dialect: Dialect) -> Any:
        return json.loads(value)


class Base(DeclarativeBase):
    """Declarative base whose metadata defines every current table."""


class SchemaRevisionRecord(Base):
    """The single Alembic revision row naming the schema revision."""

    __tablename__ = "alembic_version"

    version_num: Mapped[str] = mapped_column(Text, primary_key=True)


class ProfileRecord(Base):
    """A user profile; owns accounts and decision makers."""

    __tablename__ = "profiles"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    accounts: Mapped[List["AccountRecord"]] = relationship(back_populates="profile")
    makers: Mapped[List["DecisionMakerRecord"]] = relationship(back_populates="profile")


class AccountRecord(Base):
    """A simulated cash account; its balances come from its ledger events."""

    __tablename__ = "accounts"
    __table_args__ = (
        CheckConstraint("currency = 'USD'", name="account_currency"),
        Index("accounts_by_profile", "profile_id"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("profiles.id"), type_=Text)
    name: Mapped[str] = mapped_column(Text)
    currency: Mapped[str] = mapped_column(Text)
    profile: Mapped[ProfileRecord] = relationship(back_populates="accounts")
    runs: Mapped[List["RunRecord"]] = relationship(back_populates="account")
    events: Mapped[List["LedgerEventRecord"]] = relationship(back_populates="account")


class DecisionMakerRecord(Base):
    """A registered decision maker (human, rule, model or agent) of a profile."""

    __tablename__ = "makers"
    __table_args__ = (
        CheckConstraint("kind IN ('human','rule','model','agent')", name="maker_kind"),
        Index("makers_by_profile", "profile_id"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("profiles.id"), type_=Text)
    name: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)
    version: Mapped[str] = mapped_column(Text)
    config: Mapped[object] = mapped_column(CanonicalJSON, nullable=False)
    profile: Mapped[ProfileRecord] = relationship(back_populates="makers")
    runs: Mapped[List["RunRecord"]] = relationship(back_populates="maker")


class RunRecord(Base):
    """One experiment run of an account, with its configuration and policy state."""

    __tablename__ = "runs"
    __table_args__ = (
        CheckConstraint("status IN ('running','completed','stopped','failed')", name="run_status"),
        CheckConstraint("time_ns >= 0", name="run_time"),
        Index("one_active_run", "account_id", unique=True, sqlite_where=text("status='running'")),
        Index("runs_by_account", "account_id"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), type_=Text)
    maker_id: Mapped[str] = mapped_column(ForeignKey("makers.id"), type_=Text)
    status: Mapped[str] = mapped_column(Text)
    time_ns: Mapped[int] = mapped_column(Integer)
    universe: Mapped[list] = mapped_column(CanonicalJSON, nullable=False)
    config: Mapped[object] = mapped_column(CanonicalJSON, nullable=False)
    initial_snapshot: Mapped[dict] = mapped_column(CanonicalJSON, nullable=False)
    policy_state: Mapped[object] = mapped_column(CanonicalJSON, nullable=False)
    account: Mapped[AccountRecord] = relationship(back_populates="runs")
    maker: Mapped[DecisionMakerRecord] = relationship(back_populates="runs")
    events: Mapped[List["LedgerEventRecord"]] = relationship(back_populates="run")


class LedgerEventRecord(Base):
    """One append-only account ledger event; the authoritative history."""

    __tablename__ = "events"
    __table_args__ = (
        UniqueConstraint("account_id", "event_id"),
        CheckConstraint("sequence > 0", name="event_sequence"),
        CheckConstraint(
            "kind IN ('opened','intent','accepted','rejected','fill',"
            "'cancelled','execution_tick','execution_end')",
            name="event_kind",
        ),
        CheckConstraint(
            "(run_id IS NULL AND time_ns IS NULL AND kind='opened') OR "
            "(run_id IS NOT NULL AND time_ns IS NOT NULL AND time_ns >= 0 AND kind<>'opened')",
            name="event_time",
        ),
        Index("events_by_run", "run_id"),
    )

    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), type_=Text, primary_key=True)
    sequence: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[str] = mapped_column(Text)
    run_id: Mapped[Optional[str]] = mapped_column(ForeignKey("runs.id"), type_=Text)
    time_ns: Mapped[Optional[int]] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(Text)
    data: Mapped[dict] = mapped_column(CanonicalJSON, nullable=False)
    account: Mapped[AccountRecord] = relationship(back_populates="events")
    run: Mapped[Optional[RunRecord]] = relationship(back_populates="events")


class AccountCheckpointRecord(Base):
    """Derived account state header, rebuilt from the ledger when stale."""

    __tablename__ = 'account_checkpoints'
    account_id: Mapped[str] = mapped_column(ForeignKey('accounts.id'), type_=Text, primary_key=True)
    state: Mapped[dict] = mapped_column(CanonicalJSON, nullable=False)


class DecisionRecord(Base):
    """One decision audit record of a run, in decision order."""

    __tablename__ = 'decision_records'
    __table_args__ = (CheckConstraint('decision_index > 0', name='decision_index'),)
    run_id: Mapped[str] = mapped_column(ForeignKey('runs.id'), type_=Text, primary_key=True)
    decision_index: Mapped[int] = mapped_column(Integer, primary_key=True)
    data: Mapped[dict] = mapped_column(CanonicalJSON, nullable=False)


class LedgerItemRecord(Base):
    """Derived intent/order projection; rebuilt from the authoritative ledger."""

    __tablename__ = 'ledger_items'
    account_id: Mapped[str] = mapped_column(ForeignKey('accounts.id'), type_=Text, primary_key=True)
    kind: Mapped[str] = mapped_column(Text, primary_key=True)
    item_id: Mapped[str] = mapped_column(Text, primary_key=True)
    status: Mapped[str] = mapped_column(Text)
    data: Mapped[dict] = mapped_column(CanonicalJSON, nullable=False)
    __table_args__ = (Index('active_ledger_items', 'account_id', 'status'),)


class ResearchArchiveRecord(Base):
    """Per-run archive of market, final, execution and Agent operation data."""

    __tablename__ = 'research_archives'
    run_id: Mapped[str] = mapped_column(ForeignKey('runs.id'), type_=Text, primary_key=True)
    data: Mapped[dict] = mapped_column(CanonicalJSON, nullable=False)


class RunCheckpointRecord(Base):
    """Latest durable execution position; ordinary market frames stay in memory."""

    __tablename__ = 'run_checkpoints'
    run_id: Mapped[str] = mapped_column(ForeignKey('runs.id'), type_=Text, primary_key=True)
    version: Mapped[int] = mapped_column(Integer)
    state: Mapped[dict] = mapped_column(CanonicalJSON, nullable=False)


class AgentRevisionRecord(Base):
    """One immutable saved Agent definition revision (append-only)."""

    __tablename__ = 'agent_revisions'
    __table_args__ = (
        CheckConstraint('revision > 0', name='agent_revision_positive'),
        Index('agent_revisions_by_profile', 'profile_id', 'agent_id'),
    )
    agent_id: Mapped[str] = mapped_column(Text, primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey('profiles.id'), type_=Text)
    definition: Mapped[dict] = mapped_column(CanonicalJSON, nullable=False)


class AgentExperimentRecord(Base):
    """One immutable prepared Agent experiment (append-only)."""

    __tablename__ = 'agent_experiments'
    __table_args__ = (
        ForeignKeyConstraint(
            ['agent_id', 'revision'], ['agent_revisions.agent_id', 'agent_revisions.revision']
        ),
        Index('agent_experiments_by_profile', 'profile_id', 'id'),
    )
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey('profiles.id'), type_=Text)
    account_id: Mapped[str] = mapped_column(ForeignKey('accounts.id'), type_=Text)
    agent_id: Mapped[str] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(Integer)
    config: Mapped[dict] = mapped_column(CanonicalJSON, nullable=False)
    initial_snapshot: Mapped[dict] = mapped_column(CanonicalJSON, nullable=False)
    execution_config: Mapped[dict] = mapped_column(CanonicalJSON, nullable=False)
