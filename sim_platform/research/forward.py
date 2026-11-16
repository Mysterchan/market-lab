"""Forward testing: run a method on market data as it arrives, in shadow mode.

A forward test is the only fully out-of-sample test: the data did not exist when the
method was built. ``ForwardTest`` polls a bar feed; whenever new bars arrive it appends
them to a growing price-bar recording and runs the method over just those bars on the
same simulated account, so positions and cash carry from one segment to the next. Each
segment is an ordinary run with its own lineage, and an append-only arrival log records
when each batch of bars arrived (wall-clock UTC), the recording's digest at that time
and the run that traded it, so anyone can check that every decision came after its data.

Shadow mode is the only mode: Market Lab has no broker connection, so orders always go
to the simulated execution and never to a real market.

A feed is any object with ``bars(after)`` returning bar rows (``date`` and, for
intraday bars, ``time``; ``open``, ``high``, ``low``, ``close``, ``volume``) newer than
the ISO date-time text ``after`` (None for all). ``CsvFeed`` reads a vendor CSV that an
external job keeps up to date. Method state does not carry between segments, except an
Agent's notes, which it can read back with ``journal`` tools; rules start fresh.
"""

from __future__ import annotations

import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Protocol, Union
import os

from sim_platform.domain.models import ContractError

PathInput = Union[str, 'os.PathLike[str]']
COLUMNS = ('date', 'time', 'open', 'high', 'low', 'close', 'volume')
DAY_NS = 86_400 * 10**9
# A daily bar is placed in the regular session: open 09:30, close just before 16:00.
DAILY_OPEN_NS = (9 * 3600 + 30 * 60) * 10**9
DAILY_CLOSE_NS = 16 * 3600 * 10**9


class BarFeed(Protocol):
    """New bars after a point in time; may return nothing."""

    def bars(self, after: Optional[str]) -> Iterable[Mapping[str, Any]]: ...


def _stamp(row: Mapping[str, Any]) -> str:
    time_text = str(row.get('time') or '').strip()
    return f'{str(row["date"]).strip()}T{time_text}' if time_text else str(row['date']).strip()


class CsvFeed:
    """Bars from a CSV (Date[,Time],Open,High,Low,Close,Volume) that grows over time."""

    def __init__(self, path: PathInput) -> None:
        self.path = Path(path)

    def bars(self, after: Optional[str]) -> List[Dict[str, Any]]:
        with self.path.open(newline='', encoding='utf-8') as stream:
            rows = [
                {k.strip().lower(): v for k, v in row.items()} for row in csv.DictReader(stream)
            ]
        rows.sort(key=_stamp)
        return [row for row in rows if after is None or _stamp(row) > after]


def _parse(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp) if 'T' in stamp else datetime.fromisoformat(stamp)


class ForwardTest:
    """Shadow trading on arriving bars: one run per batch, on one account.

    Args:
        platform: An open Platform.
        profile: Profile that owns the account and Agent.
        account: Simulated account that carries positions between segments.
        feed: The bar source (``BarFeed``).
        folder: Where the growing recording and the arrival log are kept.
        symbol: The symbol the bars describe.
        method: 'rule', 'hold', 'model' or 'agent'.
        settings: The method's settings; for an Agent, complete Agent settings whose
            ``start_ns``/``end_ns`` are replaced for each segment.
        agent_id, revision: The saved Agent (method 'agent').
        configure: For an Agent, ``configure(experiment)`` configures its model (for
            example ``lambda e: app.configure_experiment_model(e, profile)``).
        execution_config: Execution settings for every segment.
    """

    def __init__(
        self,
        platform: Any,
        profile: str,
        account: str,
        feed: BarFeed,
        folder: PathInput,
        *,
        symbol: str,
        method: str = 'rule',
        settings: Optional[Dict[str, Any]] = None,
        agent_id: Optional[str] = None,
        revision: Optional[int] = None,
        configure: Optional[Callable[[Any], Any]] = None,
        execution_config: Optional[Any] = None,
    ) -> None:
        if method == 'agent' and (agent_id is None or revision is None or configure is None):
            raise ContractError('an Agent forward test needs agent_id, revision and configure')
        self.platform, self.profile, self.account, self.feed = platform, profile, account, feed
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.symbol, self.method, self.settings = symbol, method, dict(settings or {})
        self.agent_id, self.revision, self.configure = agent_id, revision, configure
        self.execution_config = execution_config
        self.recording = self.folder / f'{symbol}-forward.csv'
        self.log = self.folder / f'{symbol}-forward-log.jsonl'

    # ------------------------------------------------------------------ state

    def segments(self) -> List[Dict[str, Any]]:
        """Every recorded segment: arrival time, bars, recording digest and run ID."""
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines() if line]

    def _last(self) -> Optional[str]:
        segments = self.segments()
        return segments[-1]['last_bar'] if segments else None

    def _first(self) -> Optional[str]:
        segments = self.segments()
        return segments[0]['first_bar'] if segments else None

    def _append(self, rows: List[Mapping[str, Any]]) -> None:
        """Append bars to the recording, never rewriting what earlier segments traded."""
        exists = self.recording.exists()
        if exists:
            with self.recording.open(newline='', encoding='utf-8') as stream:
                columns = [c.strip().lower() for c in next(csv.reader(stream))]
        else:
            intraday = any(str(row.get('time') or '').strip() for row in rows)
            columns = [c for c in COLUMNS if intraday or c != 'time']
        with self.recording.open('a', newline='', encoding='utf-8') as stream:
            writer = csv.writer(stream)
            if not exists:
                writer.writerow([c.capitalize() for c in columns])
            for row in rows:
                writer.writerow([row.get(c, '') or '' for c in columns])
            stream.flush()
            os.fsync(stream.fileno())

    def _window(self, first: str, last: str, daily: bool) -> Dict[str, int]:
        """Session nanoseconds (from midnight of the recording's first day) of new bars."""
        origin = _parse(self._first() or first).date()

        def offset(stamp: str, clock_ns: int) -> int:
            moment = _parse(stamp)
            day = (moment.date() - origin).days * DAY_NS
            if daily:
                return day + clock_ns
            seconds = moment.hour * 3600 + moment.minute * 60 + moment.second
            return day + seconds * 10**9

        return dict(
            start_ns=offset(first, DAILY_OPEN_NS),
            end_ns=offset(last, DAILY_CLOSE_NS) + (0 if daily else 60 * 10**9),
        )

    # ------------------------------------------------------------------- work

    def step(self) -> Optional[Dict[str, Any]]:
        """Poll the feed once; trade any new bars as one segment. None when nothing new."""
        last = self._last()
        rows = sorted(self.feed.bars(last), key=_stamp)
        rows = [row for row in rows if last is None or _stamp(row) > last]
        if not rows:
            return None
        arrived = datetime.now(timezone.utc).isoformat(timespec='seconds')
        self._append(rows)
        first, final = _stamp(rows[0]), _stamp(rows[-1])
        daily = 'T' not in first
        window = self._window(first, final, daily)
        digest = hashlib.sha256(self.recording.read_bytes()).hexdigest()
        result = self._run(window, first, daily)
        segment = dict(
            arrived_at=arrived,
            first_bar=first,
            last_bar=final,
            bars=len(rows),
            recording_sha256=digest,
            run_id=result['run_id'],
            status=result['status'],
            window=window,
        )
        with self.log.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(segment, sort_keys=True) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        return segment

    def _run(self, window: Dict[str, int], first: str, daily: bool) -> Dict[str, Any]:
        options: Dict[str, Any] = dict(
            source=self.recording,
            method=self.method,
            execution_config=self.execution_config,
        )
        if self.method == 'agent':
            settings = dict(
                self.settings,
                symbol=self.symbol,
                start_ns=str(window['start_ns']),
                end_ns=str(window['end_ns']),
            )
            options.update(
                settings=settings,
                agent_id=self.agent_id,
                revision=self.revision,
                execution_mode='live',
            )
        else:
            moment = _parse(first)
            time_of_day = (
                DAILY_OPEN_NS
                if daily
                else (moment.hour * 3600 + moment.minute * 60 + moment.second) * 10**9
            )
            options.update(
                settings=dict(
                    self.settings,
                    symbol=self.symbol,
                    start_mode='custom',
                    start_date=moment.date().isoformat(),
                    start_time_ns=time_of_day,
                ),
                execution_mode='controlled',
            )
        experiment = self.platform.open_experiment(self.profile, self.account, **options)
        try:
            if self.method == 'agent':
                assert self.configure is not None
                self.configure(experiment)
                experiment.play()
                while experiment.runner.active or not experiment.runner.archive_ready:
                    experiment.tick(render=False)
                    time.sleep(0.02)
                experiment.tick(render=False)
            else:
                while experiment.runner.active:
                    experiment.step()
            report = experiment.report()
            return dict(run_id=experiment.run_id, status=report['run']['status'])
        finally:
            experiment.close()

    def run(
        self,
        *,
        poll_seconds: float = 60,
        until: Optional[Callable[[], bool]] = None,
        segments: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Poll every ``poll_seconds`` until ``until()`` is true or ``segments`` ran."""
        done: List[Dict[str, Any]] = []
        while not (until is not None and until()) and (segments is None or len(done) < segments):
            segment = self.step()
            if segment is not None:
                done.append(segment)
                continue
            time.sleep(poll_seconds)
        return done
