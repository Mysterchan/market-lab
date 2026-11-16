"""Inspect the storage location without opening or creating a database."""

from __future__ import annotations

from typing import TYPE_CHECKING

import argparse

from ..context import CliContext
from ..output import emit_json

if TYPE_CHECKING:  # Annotation only.
    from ..arguments import Subcommands


def register(commands: Subcommands) -> None:
    command = commands.add_parser("path", help="show location without creating files")
    command.set_defaults(handler=show_path)


def show_path(args: argparse.Namespace, context: CliContext) -> int:
    directory = context.resolve_directory()
    return emit_json(
        {"data_directory": str(directory), "database": str(directory / "platform.sqlite")}
    )
