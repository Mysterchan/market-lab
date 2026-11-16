"""Compose original-operation playback with the existing financial engine."""

from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Union, cast

from sim_platform.domain.models import ContractError
from sim_platform.resources import cleanup_after_error
from sim_platform.market.prepared.reader import PreparedMarket
from sim_platform.market.prepared.session import PreparedSession
from sim_platform.market.prepared.publications import PublicationReader
from sim_platform.market.models import NewsRecord
from sim_platform.decisions.policy.models import DecisionConfig
from sim_platform.decisions.policy.policies import HumanPolicy
from sim_platform.execution.models import ExecutionConfig
from sim_platform.replay.comparison import financial_values, financial_events
from sim_platform.replay.commands import apply_recorded_command
from sim_platform.replay.plan import decode_plan

if TYPE_CHECKING:  # Annotation only.
    from sim_platform.decisions.policy.contracts import Policy
    from sim_platform.decisions.policy.runner import PolicyDecisionRunner
    from sim_platform.replay.plan import RecordedCommand
    from ..execution_operations import ApplicationOperations

    PreparedReader = Union[PreparedMarket, PublicationReader]
    # Creates the destination runner: new_runner(profile, run_id, maker, market, policy).
    ReplayRunnerFactory = Callable[[str, str, str, PreparedSession, Policy], PolicyDecisionRunner]

# The outcome: 'run_id', 'matched', 'commands', 'financial', 'replay', 'model_calls',
# 'events', 'operations' and 'events_verified'.
ReplayResult = Dict[str, Any]


def validate_destination(
    operations: ApplicationOperations, profile: str, account: str, seed: Dict[str, Any]
) -> None:
    """Refuse a destination whose initial financial state differs before creating a run."""
    initial = seed['initial_account']
    actual = asdict(operations.snapshot(profile, account))
    if actual['currency'] != initial['currency'] or financial_values(actual) != financial_values(
        initial
    ):
        raise ContractError('Replay destination must match the original initial account')


def validate_source(
    reader: PreparedReader,
    commands: List[RecordedCommand],
    terminal: Dict[str, Any],
    cancelled: Callable[[], bool],
) -> None:
    """Check all recorded source prefixes before any destination financial action."""
    final_position = terminal['position']
    if int(final_position['boundary']) > reader.manifest['frames']:
        raise ContractError('recorded terminal prefix exceeds source')
    for command in commands:
        if cancelled():
            raise ContractError('Replay cancelled')
        prefix = reader.prefix(int(command['effective_ns']))
        if prefix != int(command['prepared_prefix']) or reader.source_sequence(prefix) != int(
            command['source_sequence']
        ):
            raise ContractError('prepared Replay source prefix differs from the record')


def apply_commands(
    operations: ApplicationOperations,
    runner: PolicyDecisionRunner,
    market: PreparedSession,
    profile: str,
    commands: List[RecordedCommand],
    seed: Dict[str, Any],
    old_orders: Dict[str, Any],
    cancelled: Callable[[], bool],
) -> None:
    """Apply recorded operations in order; model/policy code is never called."""
    run_id = runner.run_id
    new_orders: Dict[str, Any] = {}
    for command in commands:
        if cancelled():
            raise ContractError('Replay cancelled')
        stamp = int(command['effective_ns'])
        if not seed['start_ns'] <= stamp < seed['end_ns']:
            raise ContractError('recorded command outside experiment interval')
        while market.next_event_time_ns is not None and market.next_event_time_ns <= stamp:
            if cancelled():
                raise ContractError('Replay cancelled')
            runner.pump(1)
        if market.merged_prefix != int(command['prepared_prefix']):
            raise ContractError('prepared Replay source prefix differs from the record')
        position = dict(
            sequence=str(market.snapshot.sequence),
            time_ns=str(stamp),
            boundary=str(market.merged_prefix),
            ordinal=command['admission_sequence'],
        )
        if command.get('financial_effect') is False:
            continue
        apply_recorded_command(
            operations,
            runner,
            profile,
            run_id,
            position,
            command['payload'],
            old_orders,
            new_orders,
        )


def finish_and_verify(
    operations: ApplicationOperations,
    runner: PolicyDecisionRunner,
    market: PreparedSession,
    profile: str,
    archive: Dict[str, Any],
    terminal: Dict[str, Any],
    expected: Dict[str, Any],
    commands: List[RecordedCommand],
    cancelled: Callable[[], bool],
) -> ReplayResult:
    """Advance to the sealed endpoint and compare recalculated financial evidence."""
    run_id = runner.run_id
    reader = market.reader
    final_position = terminal['position']
    target = int(final_position['boundary'])
    if target > reader.manifest['frames']:
        raise ContractError('recorded terminal prefix exceeds source')
    while market.merged_prefix < target:
        if cancelled():
            raise ContractError('Replay cancelled')
        runner.pump(1)
    status = archive['end']['status']
    if market.snapshot.sequence != int(final_position['sequence']):
        raise ContractError('recorded terminal source sequence mismatch')
    runner.finish_recorded_replay(
        status, 'recorded operation Replay', time_ns=int(final_position['time_ns'])
    )
    observed = asdict(runner.snapshot)
    events = financial_events(operations.run_events(profile, run_id))
    final = next(r['details'] for r in archive['records'] if r.get('event') == 'prepared_final')
    if 'events' in final and events != final['events']:
        raise ContractError('prepared operation Replay financial events mismatch')
    if financial_values(observed) != financial_values(expected):
        raise ContractError('prepared operation Replay financial result mismatch')
    return dict(
        run_id=run_id,
        matched=True,
        commands=len(commands),
        financial=financial_values(observed),
        replay=True,
        model_calls=0,
        events=events,
        operations=commands,
        events_verified='events' in final,
    )


def _replay_merged(
    operations: ApplicationOperations,
    new_runner: ReplayRunnerFactory,
    profile: str,
    account: str,
    archive: Dict[str, Any],
    *,
    cancelled: Callable[[], bool],
) -> ReplayResult:
    """Replay a run on several symbols: one prepared artifact per symbol, merged again."""
    from sim_platform.market.prepared.merged import MergedMarket

    seed, expected, commands, terminal, old_orders = decode_plan(archive)
    sessions: List[PreparedSession] = []
    market: Any = None
    runner: Optional[PolicyDecisionRunner] = None
    run_id: Optional[str] = None
    try:
        for artifact in seed['artifacts']:
            prepared = PreparedMarket(artifact)
            try:
                prepared.validate(cancelled=cancelled)
                start = min(seed['start_ns'], prepared.manifest['last_ns'])
                sessions.append(PreparedSession.from_reader(prepared, start))
            except Exception as exc:
                cleanup_after_error(exc, prepared.close)
                raise
        market = MergedMarket.from_sessions(sessions, seed['start_ns'])
        validate_source(market.reader, commands, terminal, cancelled)
        policy = HumanPolicy()
        source = dict(
            execution_mode='live',
            prepared=market.prepared_descriptor(),
            start_origin=dict(sequence=market.snapshot.sequence, time_ns=market.time_ns),
            universe=list(market.universe),
            replay=True,
        )
        maker, run_id = operations.create_managed_decision_run(
            profile,
            account,
            list(market.universe),
            policy,
            DecisionConfig(mode='manual'),
            ExecutionConfig(**seed['execution']),
            source=source,
        )
        runner = new_runner(profile, run_id, maker, market, policy)
        runner.begin_recorded_replay()
        runner.play()
        apply_commands(operations, runner, market, profile, commands, seed, old_orders, cancelled)
        return finish_and_verify(
            operations, runner, market, profile, archive, terminal, expected, commands, cancelled
        )
    finally:
        if runner is not None:
            runner.close()
        elif run_id is not None:
            operations.abort_execution_run(profile, run_id)
        if market is not None:
            market.close()
        else:
            for session in sessions:
                session.close()


def replay(
    operations: ApplicationOperations,
    new_runner: ReplayRunnerFactory,
    profile: str,
    account: str,
    archive: Dict[str, Any],
    *,
    cancelled: Callable[[], bool] = lambda: False,
) -> ReplayResult:
    """Validate, prepare, apply and verify one sealed prepared-operation recording."""
    seed, expected, commands, terminal, old_orders = decode_plan(archive)
    validate_destination(operations, profile, account, seed)
    if seed.get('artifacts'):
        return _replay_merged(
            operations, new_runner, profile, account, archive, cancelled=cancelled
        )
    reader: PreparedReader = PreparedMarket(seed['artifact'])
    market: Optional[PreparedSession] = None
    runner: Optional[PolicyDecisionRunner] = None
    run_id: Optional[str] = None
    try:
        # The reader is still the PreparedMarket opened above.
        cast(PreparedMarket, reader).validate(cancelled=cancelled)
        if seed.get('publications'):
            reader = PublicationReader(reader, tuple(NewsRecord(**n) for n in seed['publications']))
        validate_source(reader, commands, terminal, cancelled)
        market = PreparedSession.from_reader(reader, seed['start_ns'])
        policy = HumanPolicy()
        source = dict(
            execution_mode='live',
            prepared=market.prepared_descriptor(),
            start_origin=dict(sequence=market.snapshot.sequence, time_ns=market.time_ns),
            replay=True,
        )
        maker, run_id = operations.create_managed_decision_run(
            profile,
            account,
            reader.manifest['symbol'],
            policy,
            DecisionConfig(mode='manual'),
            ExecutionConfig(**seed['execution']),
            source=source,
        )
        runner = new_runner(profile, run_id, maker, market, policy)
        runner.begin_recorded_replay()
        runner.play()
        apply_commands(operations, runner, market, profile, commands, seed, old_orders, cancelled)
        return finish_and_verify(
            operations, runner, market, profile, archive, terminal, expected, commands, cancelled
        )
    finally:
        if runner is not None:
            runner.close()
        elif run_id is not None:
            operations.abort_execution_run(profile, run_id)
        if market is not None:
            market.close()
        else:
            reader.close()
