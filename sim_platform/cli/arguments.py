"""Shared command-line value converters and argument groups."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:  # Annotation only.
    # The object returned by ArgumentParser.add_subparsers().
    Subcommands = argparse._SubParsersAction[argparse.ArgumentParser]

# Default symbol and retained book depth for market commands.
DEFAULT_SYMBOL = "AAPL"
DEFAULT_DEPTH = 5


def money(text: str) -> int:
    """argparse type: decimal USD text to money units."""
    from sim_platform.domain.inputs import money as parse_money

    try:
        return parse_money(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def add_market_arguments(
    command: argparse.ArgumentParser,
    *,
    commands_help: Optional[str] = None,
) -> None:
    command.add_argument("--input", type=Path, required=True)
    command.add_argument("--symbol", default=DEFAULT_SYMBOL)
    command.add_argument("--depth", type=int, default=DEFAULT_DEPTH)
    command.add_argument("--library", type=Path)
    command.add_argument("--news", type=Path)
    command.add_argument("--allow-implicit-eof", action="store_true")
    command.add_argument("--commands", help=commands_help)
