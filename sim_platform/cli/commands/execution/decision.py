"""Decision-specific actions and policy ownership changes."""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING, Any, Dict, List

from sim_platform.decisions import submit
from ...output import emit_event
from .actions import ExecutionCommands
from .policies import make_policy, policy_config

if TYPE_CHECKING:  # Annotation only.
    from sim_platform.application.platform import Platform
    from sim_platform.domain.models import Intent
    from sim_platform.market.playback import MarketPlayback


class DecisionCommands(ExecutionCommands):
    """Decision session commands: actions go through the policy runner, plus handoffs."""

    cancel_usage = 'cancel CLIENT_ID'

    def __init__(
        self,
        args: argparse.Namespace,
        app: Platform,
        market: MarketPlayback,
        execution: Any,
        run_id: str,
    ) -> None:
        super().__init__(args, app, market, execution, run_id)
        self.registry.add('decisions', lambda words: emit_event(self.execution.records))
        self.registry.add('handoffs', self.handoffs)
        self.registry.add(
            'handoff',
            self.handoff,
            usage='handoff MAKER POLICY [reset|restore]',
            minimum=2,
            maximum=3,
        )
        self.registry.add(
            'switch-at',
            self.switch_at,
            usage='switch-at NS MAKER POLICY [reset|restore]',
            minimum=3,
            maximum=4,
        )
        self.registry.add(
            'switch-position',
            self.switch_position,
            usage='switch-position QTY MAKER POLICY [reset|restore]',
            minimum=3,
            maximum=4,
        )

    def snapshot_extra(self) -> Dict[str, Any]:
        return {
            'maker_id': self.app.run(self.args.profile, self.run_id, include_records=False)[
                'maker_id'
            ]
        }

    def event_extra(self) -> Dict[str, Any]:
        return {'decisions': self.execution.recent_decisions(1)}

    def submit(self, intent: Intent) -> Any:
        return self.execution.act([submit(intent)])

    def cancel(self, words: List[str]) -> None:
        emit_event(self.execution.act([{'kind': 'cancel', 'client_id': words[0]}]))

    def handoffs(self, words: List[str]) -> None:
        emit_event(self.app.run(self.args.profile, self.run_id)['policy_state'])

    def handoff(self, words: List[str]) -> None:
        target, name, *state = words
        emit_event(
            self.execution.handoff(
                target,
                make_policy(name, self.args),
                policy_config(name, self.args),
                state_mode=state[0] if state else 'reset',
            )
        )

    def switch_at(self, words: List[str]) -> None:
        self.schedule(words[1:], {'kind': 'time', 'time_ns': int(words[0])})

    def switch_position(self, words: List[str]) -> None:
        self.schedule(
            words[1:],
            {
                'kind': 'position_at_least',
                'symbol': self.args.symbol,
                'quantity': int(words[0]),
            },
        )

    def schedule(self, words: List[str], condition: Dict[str, Any]) -> None:
        target, name, *state = words
        emit_event(
            self.execution.schedule_handoff(
                target,
                make_policy(name, self.args),
                condition,
                policy_config(name, self.args),
                state_mode=state[0] if state else 'reset',
            )
        )
