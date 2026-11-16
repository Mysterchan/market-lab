"""Explicit command catalog; registration order preserves the CLI help layout."""

from __future__ import annotations

from typing import TYPE_CHECKING

from . import (
    accounts,
    demos,
    desktop,
    execution,
    makers,
    market,
    paths,
    profiles,
    research,
    runs,
    studies,
)


if TYPE_CHECKING:  # Annotation only.
    from ..arguments import Subcommands

REGISTRATIONS = (
    desktop.register,
    research.register,
    market.register,
    demos.register_execution_demos,
    execution.register,
    paths.register,
    demos.register,
    profiles.register_profiles,
    profiles.register_create_profile,
    accounts.register_accounts,
    makers.register_makers,
    runs.register_runs,
    accounts.register_create_account,
    makers.register_create_maker,
    runs.register_create_run,
    accounts.register_snapshot,
    accounts.register_events,
    accounts.register_replay,
    runs.register_run,
    runs.register_finish_run,
    runs.register_submit,
    runs.register_abort_execution,
    studies.register,
)


def register_commands(commands: Subcommands) -> None:
    for register in REGISTRATIONS:
        register(commands)
