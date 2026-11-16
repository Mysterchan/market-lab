"""Replay a saved run in a separate database using only recorded decisions.

The isolated SQLite backup retains original IDs and initial account ledger prefixes,
including previous runs. Results are recalculated; original fills are only the oracle.
"""

from __future__ import annotations

from contextlib import ExitStack
from dataclasses import asdict
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    ContextManager,
    Dict,
    List,
    NoReturn,
    Optional,
    Protocol,
    Tuple,
    Union,
    cast,
)

from sim_platform.domain.models import ContractError
from sim_platform.domain.json_values import copy_json
from sim_platform.decisions import DecisionConfig
from sim_platform.resources import cleanup_after_error

if TYPE_CHECKING:  # Annotation only.
    from sim_platform.decisions.policy.contracts import Policy
    from sim_platform.decisions.policy.runner import PolicyDecisionRunner
    from sim_platform.execution.session import ExecutionSession
    from sim_platform.market.playback import MarketPlayback

    # The run's owner: a policy runner for decision runs, else a direct execution session.
    ReplayOwner = Union[PolicyDecisionRunner, ExecutionSession]
    # Opens the source market: (symbol, book depth) -> a market used as a context manager.
    MarketFactory = Callable[[str, int], ContextManager[MarketPlayback]]
    # Opens a policy runner: (profile, run_id, maker, market, policy) -> runner.
    RunnerFactory = Callable[[str, str, str, MarketPlayback, Policy], PolicyDecisionRunner]
    # Opens a direct execution session: (profile, run_id, maker, market) -> session.
    ExecutionFactory = Callable[[str, str, str, MarketPlayback], ExecutionSession]

# Recorded replay inputs read from the source database: 'profile', 'settings' (run
# config), 'records', 'journal', 'source_info', 'manifest', 'terminal', 'status',
# 'expected_end', 'expected_account' and 'expected_events'.
ReplayInputs = Dict[str, Any]
# One ordered recorded input: (ordinal, kind, record), where kind is 'decision',
# 'schedule', 'handoff', 'direct' or 'rejection'.
RecordedOperation = Tuple[int, str, Dict[str, Any]]
# A completed verification: 'verified', 'run_id', 'decisions', 'fills', 'account',
# 'position' and 'destination' (application's ReplayVerification).
Verification = Dict[str, Any]


class _Closeable(Protocol):
    def close(self) -> None: ...


# Seconds between checks while a recorded source is prepared again.
PREPARE_POLL_SECONDS = 0.01


class RecordedAdapter:
    """Stands in for the original policy: same descriptor, but decide() always fails.

    Replay supplies recorded responses, so the original policy or model is never called.
    """

    kind: str
    name: str
    version: str
    requires: Tuple[str, ...]
    parameters: Dict[str, Any]

    def __init__(self, descriptor: Dict[str, Any]) -> None:
        for key in ('kind', 'name', 'version', 'requires', 'parameters'):
            setattr(self, key, descriptor.get(key, {} if key == 'parameters' else ()))
        if 'bars' in self.requires:
            from sim_platform.decisions.policy.model_file import BarSpec

            # The recorded observations include the bars the model saw; rebuild them.
            self.bar_spec = BarSpec.recorded(self.parameters)

    def decide(self, *args: Any) -> NoReturn:
        raise AssertionError('replay must never invoke the original policy/model')


class RecoveredRun:
    """A paused reconstructed run in separate storage; caller owns its lifetime.

    close() releases ``resources``: first the ReplaySession, then (after
    adopt_lifetime) the isolated Platform root and everything it opened.
    ``platform`` is that isolated Platform, or None before adoption; typed ``Any``
    because this module may not import the application package.
    """

    def __init__(
        self,
        resources: _Closeable,
        platform: Any,
        market: MarketPlayback,
        owner: ReplayOwner,
        verification: Verification,
    ) -> None:
        self._resources, self.platform, self.market, self.owner = resources, platform, market, owner
        self.verification = verification

    def close(self) -> None:
        self._resources.close()

    def adopt_lifetime(self, resources: _Closeable, platform: Any) -> None:
        """Application explicitly transfers ownership of the isolated root."""
        self._resources, self.platform = resources, platform

    def __enter__(self) -> RecoveredRun:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


class ReplaySession:
    """Coordinate recorded input playback using application-injected resources.

    Single use: run() may be called once. The session owns the market and owner it
    opens (in an ExitStack) until close(), which is safe to repeat.

    Args:
        operations: App financial reads (DatabaseOperations); ``Any`` until the
            application package is typed.
        market_factory: Opens the source market.
        prepared_market_factory: Opens a PreparedSession for runs recorded on prepared
            data (Controlled experiments, including price-bar recordings).
        runner_factory: Opens a policy runner for decision runs.
        execution_factory: Opens an execution session for direct runs.
    """

    def __init__(
        self,
        operations: Any,
        *,
        market_factory: MarketFactory,
        runner_factory: RunnerFactory,
        execution_factory: ExecutionFactory,
        prepared_market_factory: Optional[Callable[..., Any]] = None,
    ) -> None:
        self.app = operations
        self.market_factory = market_factory
        # Prepares a recorded source again: (symbol, depth, bar_spread, bar_origin=None)
        # -> PreparedSession.
        self.prepared_market_factory = prepared_market_factory
        self.runner_factory = runner_factory
        self.execution_factory = execution_factory
        self._resources = ExitStack()
        self._on_close: Callable[[], None] = lambda: None
        self.closed = False
        self._started = False

    def on_close(self, callback: Callable[[], None]) -> None:
        self._on_close = callback

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            try:
                self._resources.close()
            finally:
                self._on_close()

    def _operations(self, inputs: ReplayInputs) -> List[RecordedOperation]:
        """Validate and merge the recorded decision/control inputs by ordinal."""
        settings, records, journal = inputs['settings'], inputs['records'], inputs['journal']
        if len(records) != journal.get('_record_count', 0):
            raise ContractError('incomplete recorded decisions')
        operations: List[RecordedOperation] = []
        for record in records:
            operations.append((record['position']['ordinal'], 'decision', record))
        for switch in journal.get('switches', []):
            operations.append((switch['position']['ordinal'], 'schedule', switch))
        for handoff in journal.get('handoffs', []):
            operations.append((handoff['position']['ordinal'], 'handoff', handoff))
        # Direct execution inputs have no decision record. Derived events are never injected.
        if 'decision' not in settings:
            for record in journal.get('execution_operations', []):
                operations.append((record['position']['ordinal'], 'direct', record))
        for record in journal.get('authority_rejections', []):
            operations.append((record['position']['ordinal'], 'rejection', record))
        operations.sort(key=lambda item: item[0])
        if len({x[0] for x in operations}) != len(operations):
            raise ContractError('ambiguous recorded operation order')
        return operations

    def _prepared_market(self, source: Dict[str, Any], origin: Dict[str, Any]) -> Any:
        """Prepare the recorded source again and position it at the run's origin.

        The new artifact must have the recorded shape; otherwise the source or the
        preparation has changed and Replay would not be comparing like with like.
        """
        from time import sleep
        from sim_platform.market import NewsRecord

        if self.prepared_market_factory is None:
            raise ContractError('this replay session cannot prepare sources')
        market = self.prepared_market_factory(
            origin['symbol'], source['depth'], source.get('bar_spread')
        )
        try:
            market.load(
                source['path'],
                news=[NewsRecord(**x) for x in source['news']],
                allow_implicit_eof=source['allow_implicit_eof'],
            )
            while not market.prepare():
                sleep(PREPARE_POLL_SECONDS)
            from sim_platform.market.prepared.session import PREPARED_SHAPE

            shape = {key: market.reader.manifest[key] for key in PREPARED_SHAPE}
            if shape != source['shape']:
                raise ContractError('replay source prepares differently than when it was run')
            market.seek(origin['time_ns'])
            if market.snapshot.sequence != origin['sequence']:
                raise ContractError('replay origin is not a prepared market position')
        except Exception as exc:
            cleanup_after_error(exc, market.close)
            raise
        return market

    def _merged_market(self, source: Dict[str, Any], origin: Dict[str, Any]) -> Any:
        """Prepare every recorded symbol again, merge them and seek the run's origin."""
        from time import sleep
        from sim_platform.market.prepared.merged import MergedMarket
        from sim_platform.market.prepared.session import PREPARED_SHAPE

        if self.prepared_market_factory is None:
            raise ContractError('this replay session cannot prepare sources')
        sessions = [
            self.prepared_market_factory(
                symbol, item['depth'], item.get('bar_spread'), item.get('bar_origin')
            )
            for symbol, item in zip(source['universe'], source['sources'])
        ]
        market = MergedMarket(sessions)
        try:
            market.load(
                [item['path'] for item in source['sources']],
                allow_implicit_eof=source['sources'][0]['allow_implicit_eof'],
            )
            while not market.prepare():
                sleep(PREPARE_POLL_SECONDS)
            for symbol, item in zip(source['universe'], source['sources']):
                manifest = market.symbol_market(symbol).reader.manifest
                if {key: manifest[key] for key in PREPARED_SHAPE} != item['shape']:
                    raise ContractError('replay source prepares differently than when it was run')
            market.seek(origin['time_ns'])
            if market.sequence != origin['sequence']:
                raise ContractError('replay origin is not a prepared market position')
        except Exception as exc:
            cleanup_after_error(exc, market.close)
            raise
        return market

    def _open_owner(self, inputs: ReplayInputs, run_id: str) -> Tuple[MarketPlayback, ReplayOwner]:
        """Acquire market then executor in the session's ExitStack."""
        from sim_platform.market import NewsRecord

        resources = self._resources
        source_info, manifest = inputs['source_info'], inputs['manifest']
        settings, profile = inputs['settings'], inputs['profile']
        origin = manifest['origin']
        if source_info.get('kind') == 'prepared':
            market = resources.enter_context(self._prepared_market(source_info, origin))
        elif source_info.get('kind') == 'prepared-merged':
            market = resources.enter_context(self._merged_market(source_info, origin))
        else:
            market = resources.enter_context(
                self.market_factory(manifest['origin']['symbol'], source_info['depth'])
            )
            market.load(
                source_info['path'],
                news=[NewsRecord(**x) for x in source_info['news']],
                allow_implicit_eof=source_info['allow_implicit_eof'],
            )
            while (
                market.snapshot.sequence < origin['sequence'] or market.time_ns < origin['time_ns']
            ):
                market.step()
        owner: ReplayOwner
        if 'decision' in settings:
            descriptor = settings['decision']['policy']
            owner = self.runner_factory(
                profile, run_id, manifest['initial_maker'], market, RecordedAdapter(descriptor)
            )
            owner.begin_recorded_replay()
        else:
            owner = self.execution_factory(profile, run_id, manifest['initial_maker'], market)
        resources.enter_context(owner)
        return market, owner

    def _apply_operations(
        self, owner: ReplayOwner, market: MarketPlayback, operations: List[RecordedOperation]
    ) -> None:
        """Apply original inputs at exact recorded boundaries, with no policy invocation."""
        from sim_platform.domain.models import Intent

        # Only decision runs record decisions, schedules, handoffs and rejections, so
        # those kinds imply a policy runner; 'direct' implies an execution session.
        policy_owner = cast('PolicyDecisionRunner', owner)
        direct_owner = cast('ExecutionSession', owner)
        for _, kind, record in operations:
            position = record['position']
            if (
                kind == 'decision'
                and record['observation']['market']['status'] == 'playing'
                and market.status != 'playing'
            ):
                owner.play()
            while owner.replay_state.get('boundary', 0) < position['boundary']:
                owner.pump(1) if market.status == 'playing' else owner.step()
            if kind == 'decision':
                observed_status = record['observation']['market']['status']
                if observed_status == 'playing' and market.status != 'playing':
                    owner.play()
                elif observed_status != 'playing' and market.status == 'playing':
                    owner.pause()
            current = owner.replay_state
            already_fired = kind == 'handoff' and policy_owner.generation == record['generation']
            expected_ordinal = current['ordinal'] if already_fired else current['ordinal'] + 1
            if position['ordinal'] != expected_ordinal:
                raise ContractError('recorded operation sequence gap')
            if (current['market_sequence'], current['market']['time_ns']) != (
                position['sequence'],
                position['time_ns'],
            ):
                raise ContractError('replay operation boundary mismatch')
            if kind == 'decision':
                actual = policy_owner.apply_recorded_decision(record)
                if (
                    actual['index'] != record['index']
                    or actual['results'] != record['results']
                    or actual['status'] != record['status']
                ):
                    raise ContractError('replay decision results mismatch')
            elif kind == 'schedule':
                policy_owner.schedule_handoff(
                    record['target'],
                    RecordedAdapter(record['policy']),
                    record['condition'],
                    DecisionConfig(**record['config']),
                    state_mode=record['state_mode'],
                )
            elif kind == 'handoff':
                # Conditional schedules fire in _advance, using the same original condition.
                if policy_owner.generation < record['generation']:
                    policy_owner.apply_recorded_handoff(
                        record, RecordedAdapter(record['to_policy']['policy'])
                    )
            elif kind == 'rejection':
                policy_owner.apply_recorded_rejection(record)
            elif kind == 'direct':
                try:
                    result = (
                        direct_owner.submit(Intent(**record['input']))
                        if record['kind'] == 'submit'
                        else direct_owner.cancel(record['input'])
                    )
                    outcome = dict(status='ok', result=copy_json(asdict(result)))
                except ContractError as exc:
                    outcome = dict(status='error', error=str(exc))
                if outcome != record['outcome']:
                    raise ContractError('replay direct operation mismatch')

    def _finish_endpoint(
        self, owner: ReplayOwner, market: MarketPlayback, inputs: ReplayInputs, recover: bool
    ) -> None:
        """Reach the recorded boundary and pause recovery or finish verification."""
        terminal, status, expected_end = (
            inputs['terminal'],
            inputs['status'],
            inputs['expected_end'],
        )
        while not owner.closed and owner.replay_state['boundary'] < terminal['boundary']:
            owner.pump(1) if market.status == 'playing' else owner.step()
        if recover:
            owner.pause()
        elif not owner.closed:
            if status == 'completed':
                (
                    owner.pump(1) if market.status == 'playing' else owner.step()
                )  # native EOF, with unchanged source sequence
            else:
                owner.finish_recorded_replay(status, expected_end['reason'])

    def _verify(
        self, owner: ReplayOwner, inputs: ReplayInputs, run_id: str
    ) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
        """Compare the recalculated account, fills, policy state and liquidity endpoint."""
        app = self.app
        profile, settings, journal = inputs['profile'], inputs['settings'], inputs['journal']
        expected_account, expected_events = inputs['expected_account'], inputs['expected_events']
        terminal = inputs['terminal']
        actual_account = copy_json(asdict(owner.snapshot.account))
        actual_events = [x['data'] for x in app.run_events(profile, run_id) if x['kind'] == 'fill']
        actual_terminal = owner.replay_state
        if actual_account != expected_account or actual_events != expected_events:
            raise ContractError('replay account or fills mismatch')
        if 'decision' in settings:
            actual_journal = app.run(profile, run_id, include_records=False)['policy_state']
            for key in (
                'state',
                '_record_count',
                'generation',
                'saved_states',
                'handoffs',
                'switches',
                'authority_rejections',
            ):
                if actual_journal.get(key) != journal.get(key):
                    raise ContractError('replay policy/handover mismatch')
        for key in ('market_sequence', 'boundary', 'tick', 'ordinal', 'bids', 'asks'):
            if actual_terminal[key] != terminal[key]:
                raise ContractError('replay liquidity/endpoint mismatch')
        return actual_account, actual_events

    def run(
        self,
        inputs: ReplayInputs,
        run_id: str,
        destination: object,
        *,
        recover: bool = False,
        policy_bindings: Optional[Dict[str, Policy]] = None,
    ) -> Union[Verification, RecoveredRun]:
        """Prepare resources, apply recorded inputs and verify without calling a model.

        recover returns a paused owned RecoveredRun. Ordinary verification returns
        detached values; the session retains resources until its caller closes it.
        """
        if self.closed or self._started:
            raise ContractError('replay session is closed or already started')
        self._started = True
        operations = self._operations(inputs)
        market, owner = self._open_owner(inputs, run_id)
        self._apply_operations(owner, market, operations)
        self._finish_endpoint(owner, market, inputs, recover)
        actual_account, actual_events = self._verify(owner, inputs, run_id)
        records, terminal, status = inputs['records'], inputs['terminal'], inputs['status']
        settings = inputs['settings']
        verification = dict(
            verified=True,
            run_id=run_id,
            decisions=len(records),
            fills=len(actual_events),
            account=actual_account,
            position=terminal,
            destination=str(destination),
        )
        if recover:
            if 'decision' in settings:
                cast('PolicyDecisionRunner', owner).resume_recorded_replay(policy_bindings)
            return RecoveredRun(self, None, market, owner, verification)
        if owner.snapshot.status != status:
            raise ContractError('replay terminal status mismatch')
        return verification
