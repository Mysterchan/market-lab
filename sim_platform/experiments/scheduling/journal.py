"""Bounded canonical notification journal; never substitute latest state for gaps."""

from __future__ import annotations

from collections import deque
from typing import Any, Deque, Dict, List

from sim_platform.domain.json_values import copy_json
from sim_platform.domain.models import ContractError, integer

# Notifications retained for resynchronization when no capacity is given.
DEFAULT_JOURNAL_CAPACITY = 128

# One journal entry: 'event_sequence', 'observation' and 'events'.
JournalEntry = Dict[str, Any]


class EventJournal:
    """The most recent market notifications, numbered from 1, kept for resync.

    The experiment binding creates one per run and appends to it; it is never shared
    between runs. A cursor older than the retained window is refused.
    """

    def __init__(self, capacity: int = DEFAULT_JOURNAL_CAPACITY) -> None:
        integer(capacity, 'journal capacity', 1)
        self.capacity = capacity
        self._values: Deque[JournalEntry] = deque(maxlen=capacity)
        self.sequence = 0

    def append(self, observation: Any, events: Any) -> JournalEntry:
        self.sequence += 1
        value = dict(
            event_sequence=self.sequence,
            observation=copy_json(observation),
            events=copy_json(events),
        )
        self._values.append(value)
        return copy_json(value)

    def after(self, sequence: int) -> List[JournalEntry]:
        """Entries after ``sequence``; raise if any of them was already dropped."""
        integer(sequence, 'notification sequence')
        if sequence > self.sequence:
            raise ContractError('notification cursor is ahead of journal')
        if self._values and sequence < self._values[0]['event_sequence'] - 1:
            raise ContractError('notification gap exceeds retained journal; evaluation invalid')
        return [copy_json(v) for v in self._values if v['event_sequence'] > sequence]
