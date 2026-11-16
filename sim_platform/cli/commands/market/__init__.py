"""Account-free market playback command."""

from __future__ import annotations

from typing import TYPE_CHECKING

import argparse

from ...arguments import add_market_arguments
from ...context import CliContext

if TYPE_CHECKING:  # Annotation only.
    from ...arguments import Subcommands


def register(commands: Subcommands) -> None:
    command = commands.add_parser("market", help="Account-free market playback")
    add_market_arguments(
        command, commands_help="semicolon-separated commands; otherwise interactive"
    )
    command.set_defaults(handler=play_market)


def play_market(args: argparse.Namespace, context: CliContext) -> int:
    from .session import run_market

    return run_market(args, context)
