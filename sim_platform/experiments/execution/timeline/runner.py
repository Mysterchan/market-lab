"""Merge prepared source events and receipt-ordered commands on one owner.

Injected advance/apply/finish retain the existing financial engine/UOW. The
shared run lifecycle owns this sequencer; there is no second OMS or book replay.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Union

from sim_platform.domain.models import ContractError

if TYPE_CHECKING:  # Annotation only.
    from sim_platform.market.prepared.publications import PublicationReader
    from sim_platform.market.prepared.reader import PreparedMarket
    from .sequencer import Command, Sequencer

    # The prepared source index; PublicationReader adds news to a PreparedMarket.
    TimelineReader = Union[PreparedMarket, PublicationReader]


class ExecutionTimeline:
    """Interleaves prepared market events with admitted commands in time order.

    The ExperimentSession creates one and its execution owner thread is the only
    caller of turn(). ``cursor`` is the number of market events applied so far.

    Args:
        reader: Prepared source event index.
        sequencer: Admission queue that hands out command batches.
        advance: Applies market event ``index`` stamped ``time_ns``.
        apply: Applies one command and returns its result.
        finish: Called once with the terminal reason and end time.
        start_ns: Experiment start time; earlier events are already applied.
        advance_span: Optionally applies a run of idle events at once and returns how many.
        after_market: Called after each market advance.
    """

    def __init__(
        self,
        reader: TimelineReader,
        sequencer: Sequencer,
        advance: Callable[[int, int], Any],
        apply: Callable[[Command], Dict[str, Any]],
        finish: Callable[[str, int], None],
        *,
        start_ns: int = 0,
        advance_span: Optional[Callable[[int, int], int]] = None,
        after_market: Optional[Callable[[], None]] = None,
    ) -> None:
        self.reader, self.sequencer = reader, sequencer
        self.advance, self.apply, self.finish = advance, apply, finish
        self.cursor = reader.prefix(start_ns)
        self.frontier_ns = start_ns
        self._ended = False
        self.trace: List[Dict[str, Any]] = []
        self.advance_span = advance_span
        self.after_market: Callable[[], None] = after_market or (lambda: None)

    def _market_through(self, stamp: int, inclusive: bool, *, end_drain: bool = False) -> bool:
        """Apply market events up to ``stamp``; False if the timeline stopped first."""
        limit = self.reader.prefix(stamp if inclusive else max(0, stamp - 1))
        while self.cursor < limit:
            time_ns = self.reader.event_time(self.cursor)
            if time_ns > stamp or (time_ns == stamp and not inclusive):
                break
            if end_drain:
                if self.sequencer.snapshot()['state'] != 'end':
                    return False
            elif not self.sequencer.execution_active():
                return False
            if self.advance_span is not None:
                count = self.advance_span(self.cursor, limit)
                if count:
                    self.cursor += count
                    self.frontier_ns = self.reader.event_time(self.cursor - 1)
                    self.after_market()
                    continue
            # The financial cursor decodes the event exactly once. Scheduling
            # needs only the indexed timestamp, not a second book snapshot.
            self.advance(self.cursor, time_ns)
            self.cursor += 1
            self.frontier_ns = time_ns
            self.after_market()
        return True

    def _terminal(self) -> None:
        if not self._ended:
            state = self.sequencer.snapshot()['state']
            if state == 'end':
                # Natural end invalidates unstarted commands, not the market
                # events needed to settle already accepted orders before end.
                self._market_through(self.sequencer.end_ns, False, end_drain=True)
                state = self.sequencer.snapshot()['state']
            self.finish(state, self.sequencer.end_ns if state == 'end' else self.frontier_ns)
            self._ended = True

    def turn(self) -> None:
        """Claim one command batch and apply it with the market events before each command."""
        if self._ended:
            raise ContractError('execution timeline ended')
        if self.sequencer.snapshot()['state'] != 'running':
            self._terminal()
            return
        batch = self.sequencer.claim()
        if batch is None:
            self._terminal()
            return
        try:
            for command in batch.commands:
                if not self._market_through(command.time_ns, True) or not self.sequencer.begin(
                    command
                ):
                    break
                try:
                    result = self.apply(command)
                except Exception:
                    self.sequencer.complete(
                        command, dict(status='failed', reason='financial operation failed')
                    )
                    raise
                self.sequencer.complete(command, result)
                self.trace.append(
                    dict(
                        time_ns=command.time_ns,
                        sequence=command.sequence,
                        source_prefix=self.reader.source_sequence(self.cursor),
                        prepared_prefix=self.cursor,
                        request_id=command.request_id,
                    )
                )
            if self._market_through(batch.until_ns, False):
                self.frontier_ns = max(self.frontier_ns, batch.until_ns - 1)
        except Exception:
            self.sequencer.stop('failed')
            raise
        finally:
            self.sequencer.release(batch)
        if self.sequencer.snapshot()['state'] != 'running':
            self._terminal()
