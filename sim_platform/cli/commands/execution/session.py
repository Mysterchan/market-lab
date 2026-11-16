"""Bind market and account resources, then run the selected command session."""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING, Any, Tuple, Type, Union

from sim_platform.execution import ExecutionConfig
from sim_platform.market import load_news
from ...interactive import run_commands
from ...output import emit_event
from .actions import ExecutionCommands

if TYPE_CHECKING:  # Annotation only.
    from sim_platform.application.platform import Platform
    from sim_platform.decisions.policy.runner import PolicyDecisionRunner
    from sim_platform.execution.session import ExecutionSession
    from sim_platform.market.playback import MarketPlayback
    from ...context import CliContext


def _owner(
    args: argparse.Namespace, app: Platform, market: MarketPlayback, config: ExecutionConfig
) -> Tuple[str, Any, Type[ExecutionCommands]]:
    """Create the run and its owner: a decision runner or a plain execution session."""
    if args.command == 'decision':
        from .decision import DecisionCommands
        from .policies import make_policy, policy_config

        policy = make_policy(args.policy, args)
        run_id = app.create_decision_run(
            args.profile,
            args.account,
            args.maker,
            args.symbol,
            policy,
            policy_config(args.policy, args),
            config,
        )
        owner: Union[PolicyDecisionRunner, ExecutionSession] = app.decision_runner(
            args.profile, run_id, args.maker, market, policy
        )
        return run_id, owner, DecisionCommands

    run_id = app.create_execution_run(args.profile, args.account, args.maker, args.symbol, config)
    owner = app.execution_session(args.profile, run_id, args.maker, market)
    return run_id, owner, ExecutionCommands


def run_execution(args: argparse.Namespace, context: CliContext) -> int:
    config = ExecutionConfig(
        latency_ns=args.latency_ns,
        fee_per_share=args.fee_per_share,
        max_order_quantity=args.max_order_quantity,
    )
    with (
        context.open_platform() as app,
        app.open_market(args.symbol, depth=args.depth, library=args.library) as market,
    ):
        market.load(
            args.input,
            news=load_news(args.news) if args.news else (),
            allow_implicit_eof=args.allow_implicit_eof,
        )
        run_id, owner, command_type = _owner(args, app, market, config)
        with owner as execution:
            emit_event({'run_id': run_id, 'model': config})
            commands = command_type(args, app, market, execution, run_id)
            run_commands(
                commands.registry,
                args.commands,
                keep_reading=lambda: execution.snapshot.status == 'running',
                is_playing=lambda: market.status == 'playing',
                advance=lambda: commands.tick([]),
                failed=lambda: execution.snapshot.status == 'failed',
            )
            execution.close()
            commands.show()
            return 1 if execution.snapshot.status == 'failed' else 0
