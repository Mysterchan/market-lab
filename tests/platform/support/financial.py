"""Trusted immutable market cursor satisfying the existing ExecutionSession port."""

from dataclasses import replace

from dataclasses import asdict
from sim_platform.domain.models import ContractError
from sim_platform.resources import cleanup_after_error
from sim_platform.execution.models import ExecutionConfig
from sim_platform.experiments.execution.timeline.sequencer import Sequencer
from sim_platform.experiments.execution.timeline.runner import ExecutionTimeline
from sim_platform.experiments.execution.market import ExecutionCommands


class PreparedCursor:
    is_notifying = False

    def __init__(self, reader, start_ns=0):
        self._reader = reader
        self._snapshot = replace(reader.snapshot_at(start_ns), status='paused')
        self._index = reader.prefix(start_ns)
        self._owner = None

    @property
    def snapshot(self):
        return self._snapshot

    @property
    def time_ns(self):
        return self._snapshot.time_ns

    @property
    def status(self):
        return self._snapshot.status

    @property
    def has_execution_owner(self):
        return self._owner is not None

    def claim_execution(self, owner):
        if self._owner is not None:
            raise ContractError('prepared cursor already owned')
        self._owner = owner

    def release_execution(self, owner):
        if self._owner is owner:
            self._owner = None

    def source_descriptor(self):
        # Never claim old raw-native replay can reproduce this new timeline.
        return None

    def play(self):
        self._snapshot = replace(self._snapshot, status='playing')

    def pause(self):
        self._snapshot = replace(self._snapshot, status='paused')

    def step(self):
        event = self._reader.event(self._index)
        self._index += 1
        self._snapshot = replace(event.snapshot, status='paused')
        return replace(event, snapshot=self._snapshot)

    def close(self):
        self._snapshot = replace(self._snapshot, status='closed')


class PreparedExecutionProbe:
    def __init__(self, execution, cursor, reader, commands, maker, clock, start_ns, end_ns):
        self.execution, self.cursor = execution, cursor
        self.run_id = execution.snapshot.run_id
        self._commands, self._maker = commands, maker
        self.sequencer = Sequencer(clock, start_ns=start_ns, end_ns=end_ns)
        self.timeline = ExecutionTimeline(
            reader, self.sequencer, self._advance, self._apply, self._finish, start_ns=start_ns
        )
        self._effective_time = start_ns
        self.closed = False

    def _advance(self, index, time_ns):
        actual = self.execution.step()
        if actual.sequence != index + 1 or actual.time_ns != time_ns:
            raise ContractError('prepared execution cursor differs from timeline')

    def _apply(self, command):
        position = dict(
            sequence=str(self.cursor.snapshot.sequence),
            time_ns=str(command.time_ns),
            boundary=str(self.cursor.snapshot.sequence),
            ordinal=str(command.sequence),
        )
        try:
            result, _ = self._commands.commit(
                self._maker, self.cursor.snapshot, action=command.payload, position=position
            )
        except Exception:
            self.execution.abandon()
            raise
        self._effective_time = command.time_ns
        return asdict(result)

    def _finish(self, state, stamp):
        if not self.execution.closed:
            self.execution.finish(
                'completed' if state == 'end' else 'stopped',
                state,
                time_ns=max(stamp, self._effective_time),
            )

    def turn(self):
        return self.timeline.turn()

    def submit(self, request_id, intent):
        intent.validate()
        return self.sequencer.admit(request_id, dict(kind='submit', intent=asdict(intent)))

    def cancel(self, request_id, order_id):
        return self.sequencer.admit(request_id, dict(kind='cancel', order_id=order_id))

    def close(self):
        if self.closed:
            return
        self.sequencer.stop()
        if not self.execution.closed:
            self._finish('stop', self.timeline.frontier_ns)
        self.cursor.close()
        self.closed = True


def assemble_probe(
    operations,
    execution_factory,
    profile,
    account,
    maker,
    reader,
    clock,
    *,
    start_ns,
    end_ns,
    config=None,
):
    """Borrow ready reader; caller owns returned probe. No second Platform/root."""
    if not 0 <= start_ns < end_ns <= reader.manifest['last_ns'] + 1:
        raise ContractError('probe interval exceeds verified event coverage')
    cursor = PreparedCursor(reader, start_ns)
    config = config or ExecutionConfig()
    settings = dict(
        execution=asdict(config),
        research_source=dict(
            execution_mode='live',
            start_origin=dict(sequence=cursor.snapshot.sequence, time_ns=cursor.time_ns),
        ),
    )
    run_id = operations.create_run(profile, account, maker, [reader.manifest['symbol']], settings)
    execution = None
    try:
        execution = execution_factory(profile, run_id, maker, cursor)
        commands = ExecutionCommands(operations.commit_decision, profile, run_id)
        return PreparedExecutionProbe(
            execution, cursor, reader, commands, maker, clock, start_ns, end_ns
        )
    except Exception as exc:
        cleanup_after_error(
            exc,
            *((execution.close,) if execution is not None else ()),
            cursor.close,
            lambda: operations.abort_execution_run(profile, run_id),
        )
        raise
