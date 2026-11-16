"""Account-free market playback and its session controls."""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING, List

from sim_platform.market import MarketError, load_news
from ...interactive import CommandRegistry, command_count, run_commands
from ...market_queries import emit_market_value, register_market_queries

if TYPE_CHECKING:  # Annotation only.
    from sim_platform.market.playback import MarketPlayback
    from ...context import CliContext


class MarketCommands:
    """Session commands for account-free market playback; one per market session."""

    def __init__(self, market: MarketPlayback) -> None:
        self.market = market
        self.registry = CommandRegistry(MarketError)
        self.registry.add('step', self.step, usage='step [N]', maximum=1)
        self.registry.add('tick', self.tick, usage='tick [N]', maximum=1)
        self.registry.add('play', lambda words: emit_market_value(market.play()))
        self.registry.add('pause', lambda words: emit_market_value(market.pause()))
        self.registry.add('snapshot', lambda words: emit_market_value(market.snapshot))
        self.registry.add('run', self.run)
        register_market_queries(self.registry, market)

    def step(self, words: List[str]) -> None:
        for _ in range(command_count(words, MarketError)):
            emit_market_value(self.market.step())
            if self.market.status in ('failed', 'completed'):
                break

    def tick(self, words: List[str]) -> None:
        for event in self.market.pump(command_count(words, MarketError)):
            emit_market_value(event)

    def run(self, words: List[str]) -> None:
        if self.market.status in ('ready', 'paused'):
            self.market.play()
        while self.market.status == 'playing':
            self.tick([])


def run_market(args: argparse.Namespace, context: CliContext) -> int:
    with (
        context.open_platform(database=False) as platform,
        platform.open_market(args.symbol, depth=args.depth, library=args.library) as market,
    ):
        market.load(
            args.input,
            news=load_news(args.news) if args.news else (),
            allow_implicit_eof=args.allow_implicit_eof,
        )
        emit_market_value(market.snapshot)
        commands = MarketCommands(market)
        run_commands(
            commands.registry,
            args.commands,
            keep_reading=lambda: market.status != 'failed',
            is_playing=lambda: market.status == 'playing',
            advance=lambda: commands.tick([]),
            failed=lambda: market.status == 'failed',
        )
        return 1 if market.status == 'failed' else 0
