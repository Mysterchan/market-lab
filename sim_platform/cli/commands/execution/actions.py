"""Trading session controls; all advancement goes through the execution owner."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from typing import TYPE_CHECKING, Any, Dict, List

from sim_platform import ContractError, Intent
from ...arguments import money
from ...interactive import CommandRegistry, command_count
from ...market_queries import register_market_queries
from ...output import emit_event

if TYPE_CHECKING:  # Annotation only.
    from sim_platform.application.platform import Platform
    from sim_platform.market.playback import MarketPlayback


class ExecutionCommands:
    """Trading session commands over one run's execution owner.

    run_execution creates one per session. ``execution`` is the run's
    ExecutionSession (or, for DecisionCommands, its PolicyDecisionRunner); it is
    typed Any because the two share only part of their interface.
    """

    cancel_usage = 'cancel CLIENT_ID or ORDER_ID'

    def __init__(
        self,
        args: argparse.Namespace,
        app: Platform,
        market: MarketPlayback,
        execution: Any,
        run_id: str,
    ) -> None:
        self.args, self.app, self.market = args, app, market
        self.execution, self.run_id = execution, run_id
        self.results: Dict[str, Any] = {}
        self.registry = CommandRegistry(ContractError)
        self.registry.add('step', self.step, usage='step [N]', maximum=1)
        self.registry.add('tick', self.tick, usage='tick [N]', maximum=1)
        self.registry.add('play', lambda words: emit_event(execution.play()))
        self.registry.add('pause', lambda words: emit_event(execution.pause()))
        self.registry.add('run', self.run)
        self.registry.add('snapshot', lambda words: self.show())
        register_market_queries(self.registry, market)
        self.registry.add(
            'buy', self.buy, usage='buy CLIENT_ID QUANTITY LIMIT_USD', minimum=3, maximum=3
        )
        self.registry.add(
            'sell', self.sell, usage='sell CLIENT_ID QUANTITY LIMIT_USD', minimum=3, maximum=3
        )
        self.registry.add('cancel', self.cancel, usage=self.cancel_usage, minimum=1, maximum=1)
        self.registry.add('events', self.events)

    def snapshot_extra(self) -> Dict[str, Any]:
        return {}

    def event_extra(self) -> Dict[str, Any]:
        return {'fills': self.execution.last_fills}

    def show(self) -> None:
        emit_event(
            {
                'market': {
                    **asdict(self.market.snapshot),
                    'time_of_day': self.market.snapshot.time_of_day,
                },
                'execution': self.execution.snapshot,
                **self.snapshot_extra(),
            }
        )

    def advanced(self, event: Any) -> None:
        emit_event({'event': event, 'execution': self.execution.snapshot, **self.event_extra()})

    def buy(self, words: List[str]) -> None:
        self.order('buy', words)

    def sell(self, words: List[str]) -> None:
        self.order('sell', words)

    def order(self, side: str, words: List[str]) -> None:
        client, quantity, price = words
        try:
            limit = money(price)
        except argparse.ArgumentTypeError as exc:
            raise ContractError('invalid limit price') from exc
        intent = Intent(client, self.args.symbol, side, int(quantity), limit)
        emit_event(self.submit(intent))

    def submit(self, intent: Intent) -> Any:
        result = self.execution.submit(intent)
        self.results[intent.client_id] = result
        return result

    def cancel(self, words: List[str]) -> None:
        identifier = words[0]
        order_id = self.results[identifier].order_id if identifier in self.results else identifier
        if order_id is None:
            raise ContractError('rejected intent has no order to cancel')
        emit_event(self.execution.cancel(order_id))

    def step(self, words: List[str]) -> None:
        for _ in range(command_count(words, ContractError)):
            if self.execution.snapshot.status != 'running':
                break
            self.advanced(self.execution.step())

    def tick(self, words: List[str]) -> None:
        for _ in range(command_count(words, ContractError)):
            if self.execution.snapshot.status != 'running':
                break
            events = self.execution.pump(1)
            if not events:
                break
            self.advanced(events[0])

    def run(self, words: List[str]) -> None:
        if self.market.status in ('ready', 'paused'):
            self.execution.play()
        while self.market.status == 'playing' and self.execution.snapshot.status == 'running':
            self.tick([])

    def events(self, words: List[str]) -> None:
        emit_event(self.app.events(self.args.profile, self.args.account))
