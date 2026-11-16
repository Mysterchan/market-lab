"""Simulated execution and policy-driven decision commands."""

from __future__ import annotations

from typing import TYPE_CHECKING

import argparse

from ...arguments import add_market_arguments, money
from ...context import CliContext

if TYPE_CHECKING:  # Annotation only.
    from ...arguments import Subcommands


# Defaults for execution and decision sessions.
DEFAULT_MAX_ORDER_QUANTITY = 1_000_000
DEFAULT_QUANTITY = 10
DEFAULT_HOLD_NS = 2_000_000_000
DEFAULT_TIMEOUT_MS = 1000


def _execution_arguments(command: argparse.ArgumentParser) -> None:
    command.add_argument("--profile", required=True)
    command.add_argument("--account", required=True)
    command.add_argument("--maker", required=True)
    add_market_arguments(command)
    command.add_argument("--latency-ns", type=int, default=0)
    command.add_argument("--fee-per-share", type=money, default=0, help="USD per simulated share")
    command.add_argument("--max-order-quantity", type=int, default=DEFAULT_MAX_ORDER_QUANTITY)
    command.set_defaults(handler=play_execution)


def register(commands: Subcommands) -> None:
    """Add the 'execution' and 'decision' session commands."""
    execution = commands.add_parser("execution", help="Limit orders on a local market replay")
    _execution_arguments(execution)
    decision = commands.add_parser("decision", help="Human/rule decisions on a market replay")
    _execution_arguments(decision)
    decision.add_argument("--policy", choices=("human", "rule", "hold"), default="rule")
    decision.add_argument("--quantity", type=int, default=DEFAULT_QUANTITY)
    decision.add_argument("--hold-ns", type=int, default=DEFAULT_HOLD_NS)
    decision.add_argument("--interval-ns", type=int, default=0)
    decision.add_argument("--timeout-ms", type=int, default=DEFAULT_TIMEOUT_MS)


def play_execution(args: argparse.Namespace, context: CliContext) -> int:
    from .session import run_execution

    return run_execution(args, context)
