"""Makers command declarations and application adapters."""

from __future__ import annotations

from typing import TYPE_CHECKING

import argparse

from ..context import CliContext
from ..output import emit_json

if TYPE_CHECKING:  # Annotation only.
    from ..arguments import Subcommands


def register_makers(commands: Subcommands) -> None:
    command = commands.add_parser("makers")
    command.add_argument("--profile", required=True)
    command.set_defaults(handler=makers)


def makers(args: argparse.Namespace, context: CliContext) -> int:
    with context.open_platform() as app:
        result = app.makers(args.profile)
    return emit_json(result)


def register_create_maker(commands: Subcommands) -> None:
    command = commands.add_parser("create-maker")
    command.add_argument("--profile", required=True)
    command.add_argument("--name", required=True)
    command.add_argument("--kind", choices=["human", "rule", "model", "agent"], required=True)
    command.add_argument("--version", required=True)
    command.set_defaults(handler=create_maker)


def create_maker(args: argparse.Namespace, context: CliContext) -> int:
    with context.open_platform() as app:
        result = {"maker_id": app.create_maker(args.profile, args.name, args.kind, args.version)}
    return emit_json(result)
