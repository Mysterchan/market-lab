"""Optional desktop application entry point."""

from __future__ import annotations

from typing import TYPE_CHECKING

import argparse
from pathlib import Path

from ..context import CliContext
from sim_platform.domain.models import ContractError

if TYPE_CHECKING:  # Annotation only.
    from ..arguments import Subcommands


def register(commands: Subcommands) -> None:
    command = commands.add_parser("desktop", help="optional PySide6 local research app")
    command.add_argument("--demo", action="store_true", help="temporary synthetic data/account")
    command.add_argument("--library", type=Path)
    command.set_defaults(handler=launch_desktop)


def launch_desktop(args: argparse.Namespace, context: CliContext) -> int:
    try:
        from sim_platform.desktop.app import launch

        return launch(context.data_dir, args.library, args.demo)
    except ImportError as exc:
        raise ContractError("install requirements-desktop.txt to use the desktop") from exc
