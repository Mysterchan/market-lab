"""Acceptance demos with temporary storage by default."""

from __future__ import annotations

from typing import TYPE_CHECKING

import argparse
from pathlib import Path

from ..context import CliContext
from ..output import emit_json
from tempfile import TemporaryDirectory

if TYPE_CHECKING:  # Annotation only.
    from ..arguments import Subcommands


def register_execution_demos(commands: Subcommands) -> None:
    execution = commands.add_parser(
        "execution-demo", help="Market-driven execution demo; temporary by default"
    )
    execution.set_defaults(handler=execution_demo)
    decision = commands.add_parser(
        "decision-demo", help="Rule policy and recorded-action replay; temporary by default"
    )
    decision.add_argument("--library", type=Path)
    decision.set_defaults(handler=decision_demo)
    handoff = commands.add_parser("handoff-demo", help="Human to rule handoff and action replay")
    handoff.add_argument("--library", type=Path)
    handoff.set_defaults(handler=handoff_demo)


def register(commands: Subcommands) -> None:
    command = commands.add_parser(
        "demo", help="fixed fixture acceptance demo; temporary by default"
    )
    command.set_defaults(handler=account_demo)


def account_demo(args: argparse.Namespace, context: CliContext) -> int:
    from sim_platform.examples.demo import run_demo

    directory = context.resolve_directory()
    if context.data_dir is None:
        with TemporaryDirectory(prefix="trading-account-demo-") as temporary:
            result = run_demo(Path(temporary))
        result["temporary_data_removed"] = True
    else:
        result = run_demo(directory)
    return emit_json(result)


def execution_demo(args: argparse.Namespace, context: CliContext) -> int:
    from sim_platform.examples.execution_demo import run_execution_demo

    context.resolve_directory()
    return emit_json(run_execution_demo(context.data_dir))


def decision_demo(args: argparse.Namespace, context: CliContext) -> int:
    from sim_platform.examples.decision_demo import run_decision_demo

    context.resolve_directory()
    return emit_json(run_decision_demo(context.data_dir, args.library))


def handoff_demo(args: argparse.Namespace, context: CliContext) -> int:
    from sim_platform.examples.handoff_demo import run_handoff_demo

    context.resolve_directory()
    return emit_json(run_handoff_demo(context.data_dir, args.library))
