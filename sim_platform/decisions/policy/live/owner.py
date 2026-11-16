"""Short policy commands on the sole market owner; no policy computation here."""

from __future__ import annotations

from dataclasses import asdict
from collections import deque
from typing import TYPE_CHECKING, Any, Callable, Deque, Dict, Optional, Tuple, cast

from sim_platform.experiments.execution.market import LocalMarketExecution
from sim_platform.domain.models import ContractError
from sim_platform.domain.json_values import copy_json

if TYPE_CHECKING:  # Annotation only.
    from sim_platform.decisions.policy.models import DecisionJournal, DecisionRecord, Observation
    from sim_platform.decisions.policy.models import MarketInput
    from sim_platform.decisions.policy.runner import ActivityRecords, PolicyDecisionRunner
    from sim_platform.execution.models import ExecutionSnapshot
    from sim_platform.execution.session import ExecutionSession
    from sim_platform.market.models import MarketEvent, MarketSnapshot, MarketTrade, NewsRecord

# A captured observation awaiting the child's commit: (observation id, Observation, journal).
PolicyCapture = Tuple[str, 'Observation', 'DecisionJournal']
# A tool request from the Rule child or Human command: 'operation' ('observe', 'manual'
# or 'commit') plus 'request_id', and for commits 'observation_id' and 'response'.
PolicyRequest = Dict[str, Any]


class PolicyOwner:
    """Owner-side half of Live Human/Rule execution, called only on the market owner.

    Keeps the bounded trade/news history used for observations, the one captured
    observation awaiting a commit, and the last published financial cut. Created and
    held for the experiment's life by the Live runner assembly; it borrows the policy
    runner and owns no resources of its own.
    """

    def __init__(self, policy_runner: PolicyDecisionRunner) -> None:
        self.runner = policy_runner
        self._capture: Optional[PolicyCapture] = None
        self._number = 0
        self._view: Optional[Dict[str, Any]] = None
        self._published_execution: Optional[ExecutionSnapshot] = None
        self._published_market: Optional[MarketSnapshot] = None
        # Per order ID: a counter bumped whenever the order's values change, and its last values.
        self._versions: Dict[str, int] = {}
        self._previous: Dict[str, Dict[str, Any]] = {}
        trades, news = policy_runner.initial_history()
        self.trades: Deque[MarketTrade] = deque(trades, maxlen=policy_runner.config.history_limit)
        self.news: Deque[NewsRecord] = deque(news, maxlen=policy_runner.config.history_limit)
        self.trigger = 'market'
        self.trigger_enabled = True
        self._presentation_sequence: Optional[int] = None
        self._presentation_dirty = True
        self._presentation: ActivityRecords = {}
        self._published_key: Optional[Tuple[int, int, int, str]] = None

    def observe_event(self, event: MarketEvent, count: int = 1) -> None:
        """Record processed market events and update the trade/news history."""
        self.runner.record_market_events(count)
        self.trigger = event.kind
        self.trigger_enabled = event.kind in self.runner.config.triggers
        if event.trade is not None and event.trade.printable and event.trade.quantity:
            self.trades.append(event.trade)
        if event.broken_match_id is not None:
            self.trades = deque(
                (t for t in self.trades if t.match_id != event.broken_match_id),
                maxlen=self.runner.config.history_limit,
            )
        if event.news is not None:
            self.news.append(event.news)

    def observation(self, trigger: str) -> Observation:
        return self.runner.observed_market(trigger, self.trades, self.news)

    def publish(self, message: object) -> None:
        """Owner publishes frozen financial values only; no presentation SQL."""
        self._published_execution = self.runner.snapshot
        self._published_market = self.runner.market_snapshot

    @property
    def published(self) -> Optional[Dict[str, Any]]:
        """Caller/UI thread assembles bounded presentation from the published cut."""
        if self._published_execution is None:
            return None
        runner = self.runner
        # Both values are always published together, so the market is set here too.
        execution = self._published_execution
        market = cast('MarketSnapshot', self._published_market)
        key = (market.sequence, market.time_ns, execution.account.sequence, execution.status)
        if not self._presentation_dirty and self._published_key == key:
            return self._view
        if self._presentation_dirty or self._presentation_sequence != execution.account.sequence:
            self._presentation = runner.activity_records()
            self._presentation_sequence = execution.account.sequence
            self._presentation_dirty = False
        self._published_key = key
        run = copy_json(self._presentation['run'])
        run['status'] = execution.status
        self._view = dict(
            mode='simulation',
            preparation=None,
            market=asdict(market),
            account=asdict(execution.account),
            execution=asdict(execution),
            trades=[],
            bars=[],
            news=[],
            events=self._presentation['events'],
            decisions=self._presentation['decisions'],
            handoffs=self._presentation['handoffs'],
            run=run,
            source=run['config'].get('research_source'),
            summary=None,
        )
        return self._view

    def capture(self, name: str) -> Dict[str, Any]:
        """Detached financial values for the runtime: the account, or order versions."""
        # RuntimeBinding borrows only detached financial snapshots and versions.
        snapshot = self.runner.snapshot
        self.publish(None)
        if name == 'account.snapshot':
            return dict(
                execution=snapshot,
                market=self.runner.market_snapshot,
                trigger_enabled=self.trigger_enabled,
            )
        for order in snapshot.account.orders:
            value = asdict(order)
            if self._previous.get(order.order_id) != value:
                self._versions[order.order_id] = self._versions.get(order.order_id, 0) + 1
                self._previous[order.order_id] = value
        versions = dict(self._versions)
        return dict(execution=snapshot, order_versions=versions)

    def perform(self, request: PolicyRequest, position: Dict[str, Any]) -> Dict[str, Any]:
        """Handle one admitted owner operation: observe, a manual response, or a commit."""
        runner = self.runner
        if request['operation'] == 'observe':
            observation, journal = runner.decision_context(self.trigger)
            self._number += 1
            self._capture = (str(self._number), observation, journal)
            return dict(
                observation_id=str(self._number),
                observation=observation.to_json(),
                state=journal['state'],
            )
        if request['operation'] == 'manual':
            result: DecisionRecord = runner.apply_live_response(request['response'], position)
            self._presentation_dirty = True
            return result
        if request['operation'] != 'commit' or self._capture is None:
            raise ContractError('unknown policy operation')
        identity, observation, journal = self._capture
        if request['observation_id'] != identity:
            raise ContractError('stale policy capture')
        response = copy_json(request['response'])
        result = runner.commit_observation(observation, journal, response, position)
        self._capture = None
        self._presentation_dirty = True
        return result


class ObservedMarketExecution(LocalMarketExecution):
    """Market execution port that also reports each processed event to the PolicyOwner.

    Borrows the run's execution session and market; ``observe`` and ``publish`` are the
    owner's callbacks. Owned by the Live pipeline that drives it.
    """

    def __init__(
        self,
        execution: ExecutionSession,
        market: MarketInput,
        observe: Callable[..., None],
        publish: Callable[[object], None],
    ) -> None:
        super().__init__(execution, market)
        self.observe, self.publish = observe, publish

    def finish(self, status: str, reason: str, time_ns: Optional[int]) -> None:
        """Publish the final committed account on the owner before terminal output."""
        super().finish(status, reason, time_ns)
        self.publish(None)

    def pump(self) -> Tuple[MarketEvent, ...]:
        events = super().pump()
        for event in events:
            self.observe(event)
        return events

    def advance_idle_prefix(self, end_prefix: int) -> Tuple[int, Optional[MarketEvent]]:
        count, event = super().advance_idle_prefix(end_prefix)
        if count:
            self.observe(event, count)
        return count, event
