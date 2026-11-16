"""Runs command declarations and application adapters."""

from __future__ import annotations

from typing import TYPE_CHECKING

import argparse
from dataclasses import asdict

from ..context import CliContext
from ..output import emit_json
from ..arguments import money
from sim_platform.domain.models import Intent

if TYPE_CHECKING:  # Annotation only.
    from ..arguments import Subcommands


def register_runs(commands: Subcommands) -> None:
    command = commands.add_parser("runs")
    command.add_argument("--profile", required=True)
    command.set_defaults(handler=runs)


def runs(args: argparse.Namespace, context: CliContext) -> int:
    with context.open_platform() as app:
        result = app.runs(args.profile)
    return emit_json(result)


def register_create_run(commands: Subcommands) -> None:
    command = commands.add_parser("create-run")
    command.add_argument("--profile", required=True)
    command.add_argument("--account", required=True)
    command.add_argument("--maker", required=True)
    command.add_argument("--symbols", nargs="+", required=True)
    command.set_defaults(handler=create_run)


def create_run(args: argparse.Namespace, context: CliContext) -> int:
    with context.open_platform() as app:
        result = {"run_id": app.create_run(args.profile, args.account, args.maker, args.symbols)}
    return emit_json(result)


def register_run(commands: Subcommands) -> None:
    command = commands.add_parser("run")
    command.add_argument("--profile", required=True)
    command.add_argument("--run", required=True)
    command.set_defaults(handler=run)


def run(args: argparse.Namespace, context: CliContext) -> int:
    with context.open_platform() as app:
        result = app.run(args.profile, args.run)
    return emit_json(result)


def register_finish_run(commands: Subcommands) -> None:
    command = commands.add_parser("finish-run")
    command.add_argument("--profile", required=True)
    command.add_argument("--run", required=True)
    command.add_argument(
        "--status", choices=["completed", "stopped", "failed"], default="completed"
    )
    command.set_defaults(handler=finish_run)


def finish_run(args: argparse.Namespace, context: CliContext) -> int:
    with context.open_platform() as app:
        app.finish_run(args.profile, args.run, args.status)
        result = app.run(args.profile, args.run)
    return emit_json(result)


def register_submit(commands: Subcommands) -> None:
    command = commands.add_parser("submit")
    command.add_argument("--profile", required=True)
    command.add_argument("--maker", required=True)
    command.add_argument("--run", required=True)
    command.add_argument("--client-id", required=True)
    command.add_argument("--symbol", required=True)
    command.add_argument("--side", choices=["buy", "sell"], required=True)
    command.add_argument("--quantity", type=int, required=True)
    command.add_argument("--limit", type=money, required=True)
    command.add_argument("--time-ns", type=int, required=True)
    command.set_defaults(handler=submit)


def submit(args: argparse.Namespace, context: CliContext) -> int:
    with context.open_platform() as app:
        decision = app.decision_session(args.profile, args.run, args.maker)
        intent = Intent(args.client_id, args.symbol, args.side, args.quantity, args.limit)
        result = {"intent_id": decision.submit(intent, args.time_ns)}
    return emit_json(result)


def register_abort_execution(commands: Subcommands) -> None:
    command = commands.add_parser("abort-execution")
    command.add_argument("--profile", required=True)
    command.add_argument("--run", required=True)
    command.set_defaults(handler=abort_execution)


def abort_execution(args: argparse.Namespace, context: CliContext) -> int:
    with context.open_platform() as app:
        result = asdict(app.abort_execution_run(args.profile, args.run))
    return emit_json(result)
