"""Profiles command declarations and application adapters."""

from __future__ import annotations

from typing import TYPE_CHECKING

import argparse

from ..context import CliContext
from ..output import emit_json

if TYPE_CHECKING:  # Annotation only.
    from ..arguments import Subcommands


def register_profiles(commands: Subcommands) -> None:
    command = commands.add_parser("profiles")
    command.set_defaults(handler=profiles)


def profiles(args: argparse.Namespace, context: CliContext) -> int:
    with context.open_platform() as app:
        result = app.profiles()
    return emit_json(result)


def register_create_profile(commands: Subcommands) -> None:
    command = commands.add_parser("create-profile")
    command.add_argument("name")
    command.set_defaults(handler=create_profile)


def create_profile(args: argparse.Namespace, context: CliContext) -> int:
    with context.open_platform() as app:
        result = {"profile_id": app.create_profile(args.name)}
    return emit_json(result)
