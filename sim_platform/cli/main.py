"""Stable CLI entry point: parse, dispatch, and report expected errors."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Optional, Sequence
from sqlalchemy.exc import SQLAlchemyError

from sim_platform import __version__
from sim_platform.diagnostics import LEVELS, configure_logging
from sim_platform.domain.models import ContractError
from .context import CliContext
from .commands import register_commands


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        description="Local simulation accounts, market playback and simulated execution",
    )
    root.add_argument("--version", action="version", version="Market Lab " + __version__)
    root.add_argument(
        "--data-dir", type=Path, help="absolute override; default is OS App data directory"
    )
    root.add_argument(
        "--log-level",
        choices=LEVELS,
        help="diagnostic detail on standard error; default MARKET_LAB_LOG_LEVEL or warning",
    )
    root.add_argument(
        "--log-json", action="store_true", help="write diagnostics as one JSON object per line"
    )
    commands = root.add_subparsers(dest="command", required=True)
    register_commands(commands)
    return root


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parser().parse_args(argv)
    try:
        configure_logging(args.log_level, json_format=True if args.log_json else None)
        with CliContext(data_dir=args.data_dir) as context:
            return args.handler(args, context)
    except (ContractError, OSError, ValueError, SQLAlchemyError) as exc:
        print(f"Platform error: {exc}", file=sys.stderr)
        return 1
