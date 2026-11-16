"""Accounts command declarations and application adapters."""

from __future__ import annotations

from typing import TYPE_CHECKING

import argparse
from dataclasses import asdict

from ..context import CliContext
from ..output import emit_json
from ..arguments import money

if TYPE_CHECKING:  # Annotation only.
    from ..arguments import Subcommands


def register_accounts(commands: Subcommands) -> None:
    command = commands.add_parser("accounts")
    command.add_argument("--profile", required=True)
    command.set_defaults(handler=accounts)


def accounts(args: argparse.Namespace, context: CliContext) -> int:
    with context.open_platform() as app:
        result = app.accounts(args.profile)
    return emit_json(result)


def register_create_account(commands: Subcommands) -> None:
    command = commands.add_parser("create-account")
    command.add_argument("--profile", required=True)
    command.add_argument("--name", required=True)
    command.add_argument("--cash", type=money, required=True, help="USD, e.g. 10000.00")
    command.set_defaults(handler=create_account)


def create_account(args: argparse.Namespace, context: CliContext) -> int:
    with context.open_platform() as app:
        result = {"account_id": app.create_account(args.profile, args.name, args.cash)}
    return emit_json(result)


def register_snapshot(commands: Subcommands) -> None:
    command = commands.add_parser("snapshot")
    command.add_argument("--profile", required=True)
    command.add_argument("--account", required=True)
    command.set_defaults(handler=snapshot)


def snapshot(args: argparse.Namespace, context: CliContext) -> int:
    with context.open_platform() as app:
        result = asdict(app.snapshot(args.profile, args.account))
    return emit_json(result)


def register_events(commands: Subcommands) -> None:
    command = commands.add_parser("events")
    command.add_argument("--profile", required=True)
    command.add_argument("--account", required=True)
    command.set_defaults(handler=events)


def events(args: argparse.Namespace, context: CliContext) -> int:
    with context.open_platform() as app:
        result = app.events(args.profile, args.account)
    return emit_json(result)


def register_replay(commands: Subcommands) -> None:
    command = commands.add_parser("replay")
    command.add_argument("--profile", required=True)
    command.add_argument("--account", required=True)
    command.set_defaults(handler=replay)


def replay(args: argparse.Namespace, context: CliContext) -> int:
    with context.open_platform() as app:
        result = asdict(
            app.replay_snapshot(args.profile, args.account, app.events(args.profile, args.account))
        )
    return emit_json(result)
