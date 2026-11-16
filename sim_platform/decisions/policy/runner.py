"""Decisions run only after the market and existing orders have committed."""

from __future__ import annotations

from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    ContextManager,
    Dict,
    Iterable,
    List,
    Optional,
    Tuple,
    Union,
)

from sim_platform.domain.models import ContractError
from sim_platform.decisions.policy.models import (
    CAPABILITIES,
    DecisionConfig,
    Observation,
    fingerprint,
)
from sim_platform.decisions.policy.handoffs import DecisionRequest, StaleAuthority, decision_state

from sim_platform.decisions.policy.contracts import policy_descriptor, default_config, Policy
from sim_platform.resources import cleanup_after_error, serialized
from sim_platform.decisions.base import DecisionRunner

if TYPE_CHECKING:  # Annotation only.
    from sim_platform.decisions.policy.controls import PolicyControls
    from sim_platform.decisions.policy.handoffs import (
        HandoffCondition,
        HandoffRecord,
        SwitchRecord,
    )
    from sim_platform.decisions.policy.invocation import InvocationResult
    from sim_platform.decisions.policy.models import (
        Action,
        DecisionJournal,
        DecisionRecord,
        MarketInput,
        PolicyResponse,
    )
    from sim_platform.decisions.policy.scheduling import HandoffSchedule
    from sim_platform.decisions.service import ActionResult
    from sim_platform.execution.models import ExecutionSnapshot, Frontier
    from sim_platform.execution.session import ExecutionSession
    from sim_platform.experiments.execution.market import (
        ExecutionCommands,
        LocalMarketExecution,
    )
    from sim_platform.market.models import MarketEvent, MarketSnapshot, MarketTrade, NewsRecord
    from sim_platform.market.pacing import PlaybackSchedule

    # Opens the run's financial session: (profile, run_id, maker, market) -> session.
    ExecutionFactory = Callable[[str, str, str, MarketInput], ExecutionSession]

# Marks "no response supplied", so a supplied None is still validated as a response.
_UNSET = object()
# Run events and decision summaries shown in the background owner's presentation.
PRESENTATION_RECORD_LIMIT = 100

# Detached presentation reads for the Live owner: 'run' (the run without records),
# 'events', 'decisions' (recent summaries) and 'handoffs'.
ActivityRecords = Dict[str, Any]


def several_markets(market: MarketSnapshot) -> Optional[Tuple[MarketSnapshot, ...]]:
    """Every symbol's book when the market covers several symbols, else None."""
    books = getattr(market, 'books', None)
    return tuple(books.values()) if books and len(books) > 1 else None


class PolicyDecisionRunner(DecisionRunner):
    """Existing human/rule observation → policy → atomic decision implementation.

    Owns the run's ExecutionSession (opened through ``execution_factory``) and closes
    it on close(); it borrows the market, whose lifetime stays with the caller. All
    public mutating methods run under the session's control lock (``serialized``).
    Authority is a (maker, generation) pair that only a committed handoff changes.

    Args:
        operations: App financial reads/writes (DatabaseOperations). Typed ``Any``
            because the application package is not annotated yet.
        profile: Profile that owns the run.
        run_id: Run already configured with a decision policy and no journal.
        maker: Decision maker that currently holds authority.
        market: Market to observe and drive; borrowed.
        policy: Policy whose descriptor must equal the run's configured policy.
        execution_factory: Opens the owned ExecutionSession.
        commands: Financial commit port; defaults to ExecutionCommands over operations.
    """

    def __init__(
        self,
        operations: Any,
        profile: str,
        run_id: str,
        maker: str,
        market: MarketInput,
        policy: Policy,
        *,
        execution_factory: ExecutionFactory,
        commands: Optional[ExecutionCommands] = None,
    ) -> None:
        run = operations.run(profile, run_id)
        if 'decision' not in run['config']:
            raise ContractError('run is not configured for decisions')
        self.config = DecisionConfig(**run['config']['decision']['config'])
        descriptor = run['config']['decision']['policy']
        if descriptor != policy_descriptor(policy):
            raise ContractError('policy identity, parameters or capabilities differ from run')
        if any(c not in CAPABILITIES for c in descriptor['requires']):
            raise ContractError('required data capability is unavailable')
        if run['policy_state']:
            raise ContractError('decision restart is unsupported')
        self._app, self._profile, self._run, self._maker = operations, profile, run_id, maker
        from sim_platform.experiments.execution.market import ExecutionCommands

        self._commands = commands or ExecutionCommands(operations.commit_decision, profile, run_id)
        self._market, self._policy = market, policy
        from sim_platform.market.pacing import PlaybackSchedule
        from sim_platform.decisions.policy.controls import PolicyControls

        self._playback: PlaybackSchedule = PlaybackSchedule(market)
        self._controls: PolicyControls = PolicyControls(self, market, maker, policy)
        self._boundary = 0
        self._last_time: Optional[int] = None
        self._busy = False
        self._replaying = False
        from sim_platform.decisions.policy.scheduling import HandoffSchedule

        self._generation = 0
        self._schedule: HandoffSchedule = HandoffSchedule()
        self._execution = execution_factory(profile, run_id, maker, market)
        from sim_platform.experiments.execution.market import LocalMarketExecution

        self._market_execution: LocalMarketExecution = LocalMarketExecution(self._execution, market)
        self._on_close: Callable[[], None] = lambda: None
        self._observation_factory: Optional[Callable[[str], Observation]] = None
        self._execution.on_close(self._execution_closed)
        # Initial journal is schema-compatible with the existing finite JSON policy state.
        try:
            operations.initialize_decisions(profile, run_id, maker)
        except Exception as exc:
            cleanup_after_error(exc, self._execution.close)
            raise

    def control_boundary(self) -> ContextManager[None]:
        return self._execution.control_boundary()

    def _execution_closed(self) -> None:
        self._schedule.clear()
        # A policy that owns a resource, such as a model process, releases it here.
        close = getattr(self._policy, 'close', None)
        if callable(close):
            close()
        self._on_close()

    def on_close(self, callback: Callable[[], None]) -> None:
        self._on_close = callback

    @property
    def run_id(self) -> str:
        return self._run

    @property
    def snapshot(self) -> ExecutionSnapshot:
        return self._execution.snapshot

    @property
    def closed(self) -> bool:
        return self._execution.closed

    @property
    def active(self) -> bool:
        return not self.closed and self.snapshot.status == 'running'

    @property
    def policy_controls(self) -> PolicyControls:
        return self._controls

    @property
    def market_snapshot(self) -> MarketSnapshot:
        return self._execution.market_snapshot

    @property
    def records(self) -> List[DecisionRecord]:
        return self._app.decision_records(self._profile, self._run)

    def recent_decisions(self, limit: int = 1) -> List[Dict[str, Any]]:
        """Bounded presentation summaries; records remains the full audit API."""
        return self._app.decision_summaries(self._profile, self._run, limit=limit)

    @property
    def replay_state(self) -> Frontier:
        return self._execution.replay_state

    @property
    def generation(self) -> int:
        return self._generation

    @serialized
    def begin_recorded_replay(self) -> None:
        """Switch to Replay: later decisions come only from recorded values."""
        self._execution.validate_control()
        if self._boundary or self._app.decision_journal(self._profile, self._run).get(
            '_record_count', 0
        ):
            raise ContractError('recorded replay must begin before decisions')
        self._replaying = True

    @serialized
    def apply_recorded_operation(
        self,
        position: Dict[str, Any],
        *,
        response: Optional[PolicyResponse] = None,
        action: Optional[Action] = None,
    ) -> Union[DecisionRecord, Tuple[ActionResult, ExecutionSnapshot]]:
        """Trusted prepared Replay: supplied actions only, never invoke a policy."""
        if not self._replaying or (response is None) == (action is None):
            raise ContractError('supply one recorded response or financial action in Replay')
        if action is None:
            return self._decide('manual', response, position=position)
        self._execution.validate_control()
        return self._commands.commit(
            self._maker, self.market_snapshot, action=action, position=position
        )

    @serialized
    def apply_recorded_decision(self, record: DecisionRecord) -> DecisionRecord:
        """Replay one audited decision after checking its observation still matches."""
        if not self._replaying:
            raise ContractError('runner is not in recorded replay mode')
        observation = self.observe(record['observation']['trigger'])
        if fingerprint(observation.to_json()) != record['fingerprint']:
            raise ContractError('recorded observation mismatch')
        if record['status'] == 'ok':
            return self._decide(record['observation']['trigger'], record['response'])
        actual = self._app.commit_decision(
            self._profile,
            self._run,
            self._maker,
            self._market.snapshot,
            record,
            self._app.decision_journal(self._profile, self._run),
        )
        self._execution.fail('policy ' + record['status'])
        return actual

    @serialized
    def apply_recorded_handoff(self, record: HandoffRecord, policy: Policy) -> HandoffRecord:
        if not self._replaying:
            raise ContractError('runner is not in recorded replay mode')
        return self.handoff(
            record['to_maker'],
            policy,
            DecisionConfig(**record['to_policy']['config']),
            state_mode=record['state_mode'],
            reason=record['reason'],
            _schedule_id=record['schedule_id'],
        )

    @serialized
    def apply_recorded_rejection(self, record: Dict[str, Any]) -> None:
        """Replay one audited authority rejection (see handoffs.reject for its keys)."""
        if not self._replaying:
            raise ContractError('runner is not in recorded replay mode')
        return self._app.handoff(
            'reject',
            self._profile,
            self._run,
            record['maker_id'],
            record['generation'],
            record['reason'],
        )

    @serialized
    def finish_recorded_replay(
        self, status: str, reason: str, *, time_ns: Optional[int] = None
    ) -> ExecutionSnapshot:
        if time_ns is not None:
            if not self._replaying:
                raise ContractError('recorded finish requires Replay')
            return self._execution.finish(status, reason, time_ns=time_ns)
        return self._execution.finish_recorded_replay(status, reason)

    @serialized
    def resume_recorded_replay(self, bindings: Optional[Dict[str, Policy]] = None) -> None:
        """Leave Replay and continue live with policies bound per maker ID."""
        if not self._replaying:
            raise ContractError('runner is not in recorded replay mode')
        journal = self._app.decision_journal(self._profile, self._run)
        expected = self._app.run(self._profile, self._run, include_records=False)['config'][
            'decision'
        ]['policy']
        bindings = bindings or {}
        selected = bindings.get(self._maker)
        if selected is None and expected['kind'] == 'human':
            from sim_platform.decisions.policy.policies import HumanPolicy

            selected = HumanPolicy()
        if selected is None or policy_descriptor(selected) != expected:
            raise ContractError('supply a compatible policy binding to resume this maker')
        self._schedule.resume_bindings(journal, bindings)
        self._policy = selected
        self._replaying = False

    @property
    def handoffs(self) -> List[HandoffRecord]:
        return self._app.decision_journal(self._profile, self._run).get('handoffs', [])

    def controller(self) -> DecisionController:
        """Capture instruction authority; a handle never follows a later handoff."""
        return DecisionController(self, self._maker, self._generation)

    def _check_authority(self, maker: str, generation: int) -> None:
        """Raise StaleAuthority (after auditing it) unless maker/generation hold authority."""
        if self._execution.closed:
            raise ContractError('execution session is closed')
        status, owner, current_generation = self._app.decision_authority(self._profile, self._run)
        if status != 'running':
            raise ContractError('run is not active')
        if maker != owner or generation != current_generation:
            self._app.handoff(
                'reject', self._profile, self._run, maker, generation, 'stale instruction authority'
            )
            raise StaleAuthority('stale instruction authority')

    def observe(self, trigger: str = 'manual') -> Observation:
        self._execution.validate_control()
        if self._observation_factory is not None:
            return self._observation_factory(trigger)
        limit = self.config.history_limit
        spec = getattr(self._policy, 'bar_spec', None)
        several = several_markets(self.market_snapshot)
        wants_bars = 'bars' in self._policy.requires and spec is not None
        return Observation(
            self._boundary,
            trigger,
            self.market_snapshot,
            self.snapshot,
            self._market.trades(limit=limit),
            self._market.news(limit=limit),
            spec.completed(self._market) if spec and wants_bars and not several else None,
            several,
            spec.completed_by_symbol(self._market) if spec and wants_bars and several else None,
        )

    def _policy_due(self, time_ns: int) -> bool:
        """A policy with decision_due() chooses its own cadence, such as once per bar."""
        due = getattr(self._policy, 'decision_due', None)
        return True if due is None else bool(due(time_ns))

    def _decide(
        self,
        trigger: str,
        response: Any = _UNSET,
        request: Optional[DecisionRequest] = None,
        *,
        position: Optional[Dict[str, Any]] = None,
    ) -> DecisionRecord:
        """Invoke the policy (or use ``response``), then commit and audit the decision.

        A policy failure is committed as a non-ok record and fails the run.
        """
        if self._busy:
            raise ContractError('decision runner is already invoking a policy')
        self._check_authority(self._maker, self._generation)
        observation = self.observe(trigger) if request is None else request.observation
        journal = (
            self._app.decision_journal(self._profile, self._run)
            if request is None
            else request.journal
        )
        if journal['_record_count'] >= self.config.max_decisions:
            self._execution.fail('decision budget exhausted')
            raise ContractError('decision budget exhausted')
        observation_json = observation.to_json()
        record = {
            'index': journal['_record_count'] + 1,
            'observation': observation_json,
            'maker_id': self._maker,
            'generation': self._generation,
            'fingerprint': fingerprint(observation_json),
            'status': 'ok',
            'error': None,
            'response': None,
            'attempted_response': None,
        }
        self._busy = True
        try:
            from sim_platform.decisions.policy.invocation import invoke

            record.update(
                invoke(
                    self._policy,
                    observation,
                    journal['state'],
                    self.config,
                    response=response,
                    supplied=response is not _UNSET,
                )
            )
            record = self._commands.commit(
                self._maker, self.market_snapshot, record, journal, position=position
            )
            self._last_time = observation.market.time_ns
            if record['status'] != 'ok':
                self._execution.fail(f"policy {record['status']}")
            return record
        except StaleAuthority:
            raise
        except Exception as exc:
            if not self._execution.closed:
                cleanup_after_error(exc, lambda: self._execution.fail('decision commit failed'))
            raise
        finally:
            self._busy = False

    @property
    def execution(self) -> ExecutionSession:
        """Borrow the financial session for Platform-owned background assembly.

        The runner retains lifetime ownership. Callers must not close it independently.
        """
        return self._execution

    def bind_observations(self, factory: Callable[[str], Observation]) -> None:
        """Install the owner-local observation source once, before any work starts."""
        if self._observation_factory is not None or self._boundary or self._busy:
            raise ContractError('bind observations once before policy work')
        self._observation_factory = factory

    def initial_history(self) -> Tuple[Tuple[MarketTrade, ...], Tuple[NewsRecord, ...]]:
        """Return bounded trade/news values for the background owner's initial view."""
        limit = self.config.history_limit
        return self._market.trades(limit=limit), self._market.news(limit=limit)

    def record_market_events(self, count: int) -> None:
        """Advance the observation boundary on the sole financial owner."""
        self._boundary += count

    def observed_market(
        self, trigger: str, trades: Iterable[MarketTrade], news: Iterable[NewsRecord]
    ) -> Observation:
        """Build an observation from owner-maintained history and current finance."""
        return Observation(
            self._boundary,
            trigger,
            self.market_snapshot,
            self.snapshot,
            tuple(trades),
            tuple(news),
            None,
            # Every symbol's book in a run on several symbols; None (and absent) otherwise.
            several_markets(self.market_snapshot),
        )

    def decision_context(self, trigger: str) -> Tuple[Observation, DecisionJournal]:
        """Check authority/budget and capture the observation and policy-state baseline."""
        self._check_authority(self._maker, self.generation)
        journal = self._app.decision_journal(self._profile, self.run_id)
        if journal['_record_count'] >= self.config.max_decisions:
            self._execution.fail('decision budget exhausted')
            raise ContractError('decision budget exhausted')
        return self.observe(trigger), journal

    def apply_live_response(
        self, response: PolicyResponse, position: Dict[str, Any]
    ) -> DecisionRecord:
        """Apply a manual Live response at its admitted position through normal decisions."""
        from sim_platform.domain.json_values import copy_json

        response = copy_json(response)
        if response['state'] is None:
            response['state'] = self._app.decision_journal(self._profile, self.run_id)['state']
        return self._decide('manual', response, position=position)

    def commit_observation(
        self,
        observation: Observation,
        journal: DecisionJournal,
        response: InvocationResult,
        position: Dict[str, Any],
    ) -> DecisionRecord:
        """Commit a child-produced response against its original observation/state.

        Financial effects use the admitted command's current market/position; no policy
        is invoked here. The caller is the financial owner, not the Rule child.
        """
        record = dict(
            index=journal['_record_count'] + 1,
            observation=observation.to_json(),
            maker_id=self._maker,
            generation=self.generation,
            fingerprint=fingerprint(observation.to_json()),
            **response,
        )
        result = self._commands.commit(
            self._maker, self.market_snapshot, record, journal, position=position
        )
        if record['status'] != 'ok':
            self._execution.fail('policy ' + record['status'])
        return result

    def activity_records(self) -> ActivityRecords:
        """Read bounded detached presentation records off the financial owner."""
        run = self._app.run(self._profile, self.run_id, include_records=False)
        return dict(
            run=run,
            events=self._app.run_events(
                self._profile, self.run_id, limit=PRESENTATION_RECORD_LIMIT
            ),
            decisions=self.recent_decisions(PRESENTATION_RECORD_LIMIT),
            handoffs=run['policy_state'].get('handoffs', []),
        )

    @serialized
    def prepare(self) -> DecisionRequest:
        """Capture a manual request, allowing a delayed adapter response to be checked."""
        if self.config.mode != 'manual' or self._busy:
            raise ContractError('prepare requires an idle manual decision runner')
        self._check_authority(self._maker, self._generation)
        return DecisionRequest(
            self._maker,
            self._generation,
            self.observe('manual'),
            self._app.decision_journal(self._profile, self._run),
        )

    @serialized
    def respond(self, request: DecisionRequest, response: PolicyResponse) -> DecisionRecord:
        """Commit a response to a prepared request if nothing changed since prepare()."""
        if not isinstance(request, DecisionRequest):
            raise ContractError('expected DecisionRequest')
        self._check_authority(request.maker_id, request.generation)
        if decision_state(request.journal) != decision_state(
            self._app.decision_journal(self._profile, self._run)
        ) or fingerprint(request.observation.to_json()) != fingerprint(
            self.observe('manual').to_json()
        ):
            self._app.handoff(
                'reject',
                self._profile,
                self._run,
                request.maker_id,
                request.generation,
                'stale decision observation',
            )
            raise StaleAuthority('stale decision observation')
        return self._decide('manual', response, request)

    def _adopt_committed_handoff(
        self, maker: str, policy: Policy, config: DecisionConfig, record: HandoffRecord
    ) -> None:
        """Update local authority only after its persistent transaction succeeds."""
        self._maker, self._policy, self.config = maker, policy, config
        self._generation = record['generation']
        self._execution.adopt_maker(maker)
        self._last_time = None
        self._schedule.reconcile(self._app.decision_journal(self._profile, self._run))

    @serialized
    def handoff(
        self,
        maker: str,
        policy: Policy,
        config: Optional[DecisionConfig] = None,
        *,
        state_mode: str = 'reset',
        reason: str = 'manual',
        _schedule_id: Optional[int] = None,
    ) -> HandoffRecord:
        """Pass authority to an existing maker now; ``state_mode`` is 'reset' or 'restore'."""
        if self._busy:
            raise ContractError('handoff requires a completed decision boundary')
        self._execution.validate_control()
        config = default_config(policy) if config is None else config
        if not isinstance(config, DecisionConfig):
            raise ContractError('expected DecisionConfig')
        from dataclasses import asdict

        record = self._app.handoff(
            'switch',
            self._profile,
            self._run,
            self._maker,
            self._generation,
            self.market_snapshot,
            self._boundary,
            maker,
            policy_descriptor(policy),
            asdict(config),
            state_mode,
            reason,
            _schedule_id,
        )
        self._adopt_committed_handoff(maker, policy, config, record)
        return record

    @serialized
    def managed_handoff(
        self,
        policy: Policy,
        maker: Optional[str] = None,
        *,
        state_mode: str = 'reset',
        condition: Optional[HandoffCondition] = None,
    ) -> str:
        """Create a managed maker and its handoff together, then publish runtime state."""
        if self._busy:
            raise ContractError('handoff requires a completed decision boundary')
        self._check_authority(self._maker, self._generation)
        self._execution.validate_control()
        config = default_config(policy)
        from dataclasses import asdict

        target, record = self._app.managed_handoff(
            'switch' if condition is None else 'schedule',
            self._profile,
            self._run,
            self._maker,
            self._generation,
            self.market_snapshot,
            self._boundary,
            maker,
            policy_descriptor(policy),
            asdict(config),
            state_mode,
            'manual' if condition is None else condition,
        )
        if condition is not None:
            self._schedule.register(record, target, policy, config, state_mode)
        else:
            self._adopt_committed_handoff(target, policy, config, record)
        return target

    @serialized
    def schedule_handoff(
        self,
        maker: str,
        policy: Policy,
        condition: HandoffCondition,
        config: Optional[DecisionConfig] = None,
        *,
        state_mode: str = 'reset',
    ) -> SwitchRecord:
        """Persist a switch to ``maker`` that fires automatically when ``condition`` holds."""
        if self._busy:
            raise ContractError('schedule requires a completed decision boundary')
        self._execution.validate_control()
        config = default_config(policy) if config is None else config
        if not isinstance(config, DecisionConfig):
            raise ContractError('expected DecisionConfig')
        from dataclasses import asdict

        record = self._app.handoff(
            'schedule',
            self._profile,
            self._run,
            self._maker,
            self._generation,
            self.market_snapshot,
            self._boundary,
            maker,
            policy_descriptor(policy),
            asdict(config),
            state_mode,
            condition,
        )
        self._schedule.register(record, maker, policy, config, state_mode)
        return record

    def _switch_if_due(self) -> None:
        if not self._schedule._policies:
            return
        journal = self._app.decision_journal(self._profile, self._run)
        due = self._schedule.due(journal, lambda: self.snapshot)
        if due is not None:
            switch, (target, policy, config, mode) = due
            self.handoff(
                target,
                policy,
                config,
                state_mode=mode,
                reason=switch['condition']['kind'],
                _schedule_id=switch['id'],
            )

    @serialized
    def act(self, actions: List[Action], state: Any = None) -> DecisionRecord:
        """Commit manual actions; ``state`` None keeps the current policy state."""
        if self.config.mode != 'manual':
            raise ContractError('manual actions require manual mode')
        if state is None:
            state = self._app.decision_journal(self._profile, self._run)['state']
        return self._decide('manual', {'actions': actions, 'state': state})

    @serialized
    def decide(self) -> DecisionRecord:
        """Invoke a registered adapter at a manual boundary, including recorded actions."""
        if self.config.mode != 'manual':
            raise ContractError('explicit decisions require manual mode')
        return self._decide('manual')

    def _advance(self, method: Callable[[], MarketEvent]) -> MarketEvent:
        """Process one market event, fire due switches, then decide if it triggers one."""
        if self._busy:
            raise ContractError('cannot advance during a decision')
        event = method()
        if event.kind in ('market', 'clock', 'news'):
            self._boundary += 1
            try:
                self._switch_if_due()
            except Exception as exc:
                cleanup_after_error(exc, lambda: self._execution.fail('automatic handoff failed'))
                raise
            if (
                not self._replaying
                and self.config.mode == 'automatic'
                and event.kind in self.config.triggers
                and (
                    self._last_time is None
                    or event.time_ns - self._last_time >= self.config.interval_ns
                )
                and self._policy_due(event.time_ns)
            ):
                self._decide(event.kind)
        return event

    @serialized
    def step(self) -> MarketEvent:
        return self._advance(self._market_execution.step)

    @serialized
    def pump(self, count: int = 1) -> Tuple[MarketEvent, ...]:
        """Advance up to ``count`` events; stops early when the run or market stops."""
        if type(count) is not int or count < 1:
            raise ContractError('pump count must be positive')
        events = []
        for _ in range(count):
            if self._execution.closed or self._market.status != 'playing':
                break
            events.append(self._advance(lambda: self._market_execution.pump()[0]))
        return tuple(events)

    @serialized
    def poll(self, *, max_events: int = 1, budget_seconds: Optional[float] = None) -> None:
        self._playback.poll(self.pump, max_events=max_events, budget_seconds=budget_seconds)

    @serialized
    def play(self, speed: Optional[float] = None) -> Any:
        if self._busy:
            raise ContractError('cannot play during a decision')
        self._playback.play(speed)
        return self._execution.play()

    @serialized
    def pause(self) -> Any:
        if self._busy:
            raise ContractError('cannot pause during a decision')
        self._playback.pause()
        return self._execution.pause()

    @serialized
    def close(self) -> None:
        if self._busy:
            raise ContractError('cannot close during a decision')
        self._execution.close()


class DecisionController:
    """Maker/generation-bound public instruction handle.

    Holds no resources: every call first checks that its captured maker and generation
    still hold authority on the borrowed runner, so a handle stops working after a handoff.
    """

    def __init__(self, runner: PolicyDecisionRunner, maker: str, generation: int) -> None:
        self._runner, self._maker, self._generation = runner, maker, generation

    def control_boundary(self) -> ContextManager[None]:
        return self._runner.control_boundary()

    @serialized
    def act(self, actions: List[Action], state: Any = None) -> DecisionRecord:
        self._runner._check_authority(self._maker, self._generation)
        return self._runner.act(actions, state)

    @serialized
    def prepare(self) -> DecisionRequest:
        self._runner._check_authority(self._maker, self._generation)
        return self._runner.prepare()

    @serialized
    def respond(self, request: DecisionRequest, response: PolicyResponse) -> DecisionRecord:
        self._runner._check_authority(self._maker, self._generation)
        return self._runner.respond(request, response)

    @serialized
    def handoff(
        self, maker: str, policy: Policy, config: Optional[DecisionConfig] = None, **kwargs: Any
    ) -> HandoffRecord:
        self._runner._check_authority(self._maker, self._generation)
        return self._runner.handoff(maker, policy, config, **kwargs)

    @serialized
    def schedule_handoff(
        self,
        maker: str,
        policy: Policy,
        condition: HandoffCondition,
        config: Optional[DecisionConfig] = None,
        **kwargs: Any,
    ) -> SwitchRecord:
        self._runner._check_authority(self._maker, self._generation)
        return self._runner.schedule_handoff(maker, policy, condition, config, **kwargs)

    @serialized
    def decide(self) -> DecisionRecord:
        self._runner._check_authority(self._maker, self._generation)
        return self._runner.decide()
