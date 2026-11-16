"""Research behavior over injected operations and resource factories; no Platform construction."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Sequence, Tuple, Union, cast

from sim_platform.domain.models import ContractError
from sim_platform.domain.json_values import copy_json
from sim_platform.market import load_news
from sim_platform.decisions.policy.contracts import make_policy
from .reports import ExportWriter, Report, run_report, export_run
from .views import SessionView, session_view
from .queries import (
    NEWS_VIEW_LIMIT,
    TRADE_VIEW_LIMIT,
    ResearchQueries,
    bar_view_limit,
    daily_closes,
    symbol_markets,
)
from sim_platform.resources import cleanup, cleanup_after_error

if TYPE_CHECKING:  # Annotation only.
    from sim_platform.decisions.agent.runner import AgentDecisionRunner
    from sim_platform.decisions.policy.live.runner import LivePolicyDecisionRunner
    from sim_platform.decisions.policy.runner import PolicyDecisionRunner
    from sim_platform.execution import ExecutionConfig
    from sim_platform.experiments.scheduling.clock import SharedExperimentClock
    from sim_platform.market.native import PathInput
    from sim_platform.market.prepared.session import PreparedSession

    # The runner a controller owns after create_experiment/start.
    ResearchRunner = Union[PolicyDecisionRunner, LivePolicyDecisionRunner, AgentDecisionRunner]

# Default symbol for a research load.
DEFAULT_SYMBOL = 'AAPL'
# 09:30 Eastern, the regular session open, in nanoseconds after midnight; the same
# value as market.prepared.session.REGULAR_OPEN_NS (not imported to avoid loading it).
REGULAR_OPEN_NS = 34_200_000_000_000
# Sleep between preparation steps while a synchronous caller waits.
PREPARE_POLL_SECONDS = 0.005
# Defaults kept for the prepare_tick signature; preparation runs the market's own budget.
PREPARE_TICK_EVENTS = 5000
PREPARE_TICK_BUDGET_SECONDS = 0.035
# Starting cash for the demo account, in money-scale units.
DEMO_ACCOUNT_CASH = 100_000_000

# The selected data source: 'path', 'bytes', 'mtime_ns', 'news_path',
# 'news_identity', 'allow_implicit_eof'; after preparation also 'prepared',
# 'start_origin', 'start_mode', 'start_time_ns'; at creation 'execution_mode' and,
# for Agents, 'symbol', 'source_manifest_ref', 'experiment_start_ns'.
SourceDescriptor = Dict[str, Any]
# Preparation progress: 'status' ('preparing', 'ready', 'failed' or 'cancelled'),
# 'start_mode', 'target_time_ns' and, while preparing, 'progress'.
Preparation = Dict[str, Any]
# Market position right after preparation: 'sequence' and 'time_ns'.
PreparedOrigin = Dict[str, int]


# Backward-compatible name for existing research callers.
policy_for = make_policy


def experiment_universe(
    path: PathInput, symbols: Sequence[str], paths: Optional[Sequence[PathInput]]
) -> List[Tuple[str, Path]]:
    """(symbol, source) pairs of an experiment on several symbols, in universe order."""
    if len(symbols) < 2 or len(set(symbols)) != len(symbols):
        raise ContractError('choose two or more different symbols')
    sources = list(paths) if paths else [path] * len(symbols)
    if len(sources) != len(symbols):
        raise ContractError('give one source per symbol, or one ITCH file for all')
    return [(s, Path(p).expanduser().resolve()) for s, p in zip(symbols, sources)]


def check_start_date(start_date: Any, start_mode: str, time_of_day: Any) -> None:
    """A dated custom start needs 'YYYY-MM-DD' and a time of day below 24 hours."""
    from datetime import date
    from sim_platform.market.models import DAY_NS

    if start_mode != 'custom':
        raise ContractError('a start date needs the custom start mode')
    try:
        date.fromisoformat(start_date)
    except (TypeError, ValueError) as exc:
        raise ContractError('start date must be YYYY-MM-DD') from exc
    if not 0 <= time_of_day < DAY_NS:
        raise ContractError('with a start date, the start time is a time of day')


def dated_start(calendar: Optional[Dict[str, Any]], start_date: str, time_of_day: int) -> int:
    """Market time of a time of day on a date of a multi-day price-bar recording."""
    from datetime import date
    from sim_platform.market.models import DAY_NS

    if not calendar or not calendar.get('start_date'):
        raise ContractError('a start date needs a price-bar recording, which has dates')
    days = (date.fromisoformat(start_date) - date.fromisoformat(calendar['start_date'])).days
    if days < 0:
        raise ContractError('start date is before the recording\'s first day')
    return days * DAY_NS + time_of_day


def book_matches(requested: Any, prepared: Any) -> bool:
    """Whether a prepared source already has the requested synthetic-book options."""
    from sim_platform.market.prepared.bars import book_options

    try:
        return book_options(requested) == prepared
    except ContractError:
        return False


class ResearchController(ABC):
    """Shared source preparation, experiment lifecycle and report ownership.

    Platform selects PolicyResearchController or AgentResearchController; the
    base contains no policy/agent setup or specialized action implementation.
    Platform registers the controller as a root resource. The controller owns the
    market it prepares and the runner it creates, replaces both when another source
    is loaded, and on close() closes the runner, archives and closes the market.

    Args:
        operations: DatabaseOperations from the application package (not yet typed).
        market_factory: Creates an unloaded prepared market for a symbol and, for a
            price-bar recording, optional book options (``market_factory(symbol, book)``).
        files: DatasetFiles adapter from the persistence package (not yet typed).
        export_writer: Atomic file writer used by export().
    """

    def __init__(
        self,
        operations: Any,
        *,
        market_factory: Callable[..., PreparedSession],
        files: Any,
        export_writer: ExportWriter,
    ) -> None:
        self.app = operations
        self._files, self._export_writer = files, export_writer
        self._queries = ResearchQueries()
        self._market_factory = market_factory
        self._market_archived = False
        self._on_close: Callable[[], None] = lambda: None
        self.market: Optional[PreparedSession] = None
        self.runner: Optional[ResearchRunner] = None
        self.profile: Optional[str] = None
        self.run_id: Optional[str] = None
        self.viewed_run: Optional[str] = None
        self.source: Optional[SourceDescriptor] = None
        self.preparation: Optional[Preparation] = None
        self._prepared_origin: Optional[PreparedOrigin] = None
        self._closed = False
        self._execution_mode: Optional[str] = None

    # Typed views of state set by an earlier phase (load, prepare). They only cast and
    # do not check, so a call in the wrong phase fails as the plain attribute would.
    @property
    def _loaded_market(self) -> PreparedSession:
        return cast('PreparedSession', self.market)

    @property
    def _current_preparation(self) -> Preparation:
        return cast(Preparation, self.preparation)

    @property
    def _current_source(self) -> SourceDescriptor:
        return cast(SourceDescriptor, self.source)

    @property
    def execution_mode(self) -> Optional[str]:
        """Selected at experiment creation; the research entry has no default mode."""
        return self._execution_mode

    def on_close(self, callback: Callable[[], None]) -> None:
        self._on_close = callback

    def _check_open(self) -> None:
        if self._closed:
            raise ContractError('research controller is closed')

    def catalog(self, profile: Optional[str] = None) -> Dict[str, Any]:
        """List profiles and, for ``profile`` (default: the first), its accounts and runs."""
        self._check_open()
        profiles = self.app.profiles()
        if profile is None and profiles:
            profile = profiles[0]['id']
        return dict(
            profiles=profiles,
            profile=profile,
            accounts=self.app.accounts(profile) if profile else [],
            runs=(
                [
                    {k: r[k] for k in ('id', 'account_id', 'maker_id', 'status', 'time_ns')}
                    for r in self.app.runs(profile, include_records=False)
                ]
                if profile
                else []
            ),
        )

    def create_profile(self, name: str) -> str:
        self._check_open()
        return self.app.create_profile(name)

    def create_account(self, profile: str, name: str, cash: int) -> str:
        self._check_open()
        return self.app.create_account(profile, name, cash)

    def _active(self) -> bool:
        return self.runner is not None and self.runner.active

    @property
    def active(self) -> bool:
        return self._active()

    def _news_identity(self, news: Optional[PathInput]) -> Optional[Dict[str, Any]]:
        return self._files.identity(news) if news else None

    def view(self) -> SessionView:
        value = session_view(self._queries.capture(self))
        mode = (
            (value.get("source") or {}).get("execution_mode")
            if self.viewed_run
            else self._execution_mode
        )
        return dict(value, execution_mode=mode)

    def _archive(self) -> None:
        """Save the market tail with the run, once after the runner has finished."""
        if (
            not self.runner
            or not self.market
            or not self.runner.archive_ready
            or self.market.status in ('failed', 'closed')
        ):
            return
        if not self.runner.active and self._market_archived:
            return
        archive = dict(
            time_ns=self.market.time_ns,
            snapshot=asdict(self.market.snapshot),
            trades=[asdict(t) for t in self.market.trades(limit=TRADE_VIEW_LIMIT)],
            bars=[asdict(b) for b in self.market.bars(limit=bar_view_limit(self.market))],
            news=[asdict(n) for n in self.market.news(limit=NEWS_VIEW_LIMIT)],
            daily_closes=daily_closes(self.market),
        )
        # Several symbols: every symbol's book, bars and daily closes, so the result page
        # can value the whole account. Absent for one symbol, whose archive is unchanged.
        markets = symbol_markets(self.market, closes=True)
        if markets:
            archive['markets'] = markets
        self.app.archive_market(self.profile, self.run_id, archive)
        if not self.runner.active:
            self._market_archived = True

    def load(
        self,
        path: PathInput,
        symbol: str = DEFAULT_SYMBOL,
        news: Optional[PathInput] = None,
        allow_implicit_eof: bool = False,
    ) -> SessionView:
        """Synchronous research load; begin_prepare exposes cancellable progress."""
        return self._load_source(path, symbol, news, allow_implicit_eof, wait=True)

    def _merged_market(
        self, universe: Sequence[Tuple[str, Path]], book: Optional[Dict[str, int]]
    ) -> Any:
        """One prepared market per symbol, merged; price-bar files share a first date."""
        from sim_platform.market.prepared.bars import is_bar_source, read_bars
        from sim_platform.market.prepared.merged import MergedMarket

        bars = [is_bar_source(source) for _, source in universe]
        if any(bars) and not all(bars):
            raise ContractError('use either price-bar files or ITCH recordings for every symbol')
        origin = min(read_bars(source).start_date for _, source in universe) if all(bars) else None
        sessions = []
        try:
            for symbol, _ in universe:
                sessions.append(
                    self._market_factory(symbol, book, bar_origin=origin)
                    if origin
                    else (
                        self._market_factory(symbol, book) if book else self._market_factory(symbol)
                    )
                )
            return MergedMarket(sessions)
        except Exception as exc:
            cleanup_after_error(exc, *(session.close for session in sessions))
            raise

    def _load_source(
        self,
        path: PathInput,
        symbol: str = DEFAULT_SYMBOL,
        news: Optional[PathInput] = None,
        allow_implicit_eof: bool = False,
        *,
        wait: bool = False,
        book: Optional[Dict[str, int]] = None,
        universe: Optional[Sequence[Tuple[str, Path]]] = None,
    ) -> SessionView:
        """Load a new market, then replace (and close) the previous runner and market.

        ``book`` chooses a price-bar recording's synthetic spread and depth
        (market.prepared.bars.book_options); it is refused for an ITCH recording.
        ``universe`` lists (symbol, source path) pairs for an experiment on several
        symbols; ``path`` and ``symbol`` are then its first pair.
        """
        self._check_open()
        if self._active():
            raise ContractError('stop the active simulation before loading another file')
        path = Path(path).expanduser().resolve()
        stat = self._files.stat(path)
        news_identity = self._news_identity(news)
        from sim_platform.market.prepared.bars import book_options, is_bar_source

        book = book_options(book)
        if book is not None and not is_bar_source(path):
            raise ContractError('spread and depth can be chosen only for a price-bar recording')
        market: Any = (
            self._merged_market(universe, book)
            if universe
            else self._market_factory(symbol, book) if book else self._market_factory(symbol)
        )
        try:
            if universe:
                # One announcements file serves every symbol of the experiment.
                market.load(
                    [source for _, source in universe],
                    news=load_news(news) if news else (),
                    allow_implicit_eof=allow_implicit_eof,
                )
            else:
                market.load(
                    path,
                    news=load_news(news) if news else (),
                    allow_implicit_eof=allow_implicit_eof,
                )
            if wait:
                from time import sleep

                while not market.prepare():
                    sleep(PREPARE_POLL_SECONDS)
        except Exception as exc:
            cleanup_after_error(exc, market.close)
            raise
        try:
            if self.runner is not None:
                self.runner.close()
                self._archive()
        except Exception as exc:
            cleanup_after_error(exc, market.close)
            raise
        previous_market, previous_runner = self.market, self.runner
        self._market_archived = False
        self.runner = None
        self._execution_mode = None
        self.profile = self.run_id = self.viewed_run = None
        self.market = market
        self.preparation = self._prepared_origin = None
        self.source = dict(
            path=str(path),
            bytes=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
            news_path=str(Path(news).resolve()) if news else None,
            news_identity=news_identity,
            allow_implicit_eof=allow_implicit_eof,
        )
        if book is not None:
            self.source['book'] = book
        if universe:
            self.source['universe'] = [symbol for symbol, _ in universe]
            self.source['paths'] = [str(source) for _, source in universe]
        if market.status == 'ready':
            self.preparation = dict(status='ready', start_mode='beginning', target_time_ns=0)
            self._prepared_origin = dict(sequence=market.snapshot.sequence, time_ns=market.time_ns)
            self.source.update(
                prepared=market.prepared_descriptor(),
                start_origin=copy_json(self._prepared_origin),
                start_mode='beginning',
                start_time_ns=None,
            )
        cleanup(
            *(
                resource.close
                for resource in (previous_runner, previous_market)
                if resource is not None
            )
        )
        return self.view()

    def can_reuse_preparation(
        self,
        path: PathInput,
        symbol: str = DEFAULT_SYMBOL,
        news: Optional[PathInput] = None,
        allow_implicit_eof: bool = False,
        start_mode: str = 'first_book',
        start_time_ns: Optional[int] = None,
        book: Optional[Dict[str, int]] = None,
        start_date: Optional[str] = None,
    ) -> bool:
        """Reuse only an untouched origin with unchanged source files/settings."""
        if (
            self.runner is not None
            or self.viewed_run
            or not self.market
            or not self.source
            or (self.preparation or {}).get('status') != 'ready'
            or self.market.status not in ('ready', 'paused')
            or self._prepared_origin
            != dict(sequence=self.market.snapshot.sequence, time_ns=self.market.time_ns)
        ):
            return False
        try:
            resolved = Path(path).expanduser().resolve()
            stat = self._files.stat(resolved)
            news_identity = self._news_identity(news)
        except OSError:
            return False
        target = REGULAR_OPEN_NS if start_mode == 'regular' else start_time_ns
        return (
            str(resolved) == self.source['path']
            and stat.st_size == self.source['bytes']
            and stat.st_mtime_ns == self.source['mtime_ns']
            and news_identity == self.source.get('news_identity')
            and symbol == self.market.snapshot.symbol
            and allow_implicit_eof == self.source['allow_implicit_eof']
            and start_mode == self.source.get('start_mode')
            and target == self.source.get('start_time_ns')
            and start_date == self.source.get('start_date')
            and book_matches(book, self.source.get('book'))
        )

    def _finish_preparation(self) -> None:
        if self._loaded_market.status == 'playing':
            self._loaded_market.pause()
        self._prepared_origin = dict(
            sequence=self._loaded_market.snapshot.sequence, time_ns=self._loaded_market.time_ns
        )
        self._current_source['start_origin'] = copy_json(self._prepared_origin)
        self._current_preparation['status'] = 'ready'

    def begin_prepare(
        self,
        path: PathInput,
        symbol: str = DEFAULT_SYMBOL,
        news: Optional[PathInput] = None,
        allow_implicit_eof: bool = False,
        start_mode: str = 'first_book',
        start_time_ns: Optional[int] = None,
        *,
        book: Optional[Dict[str, int]] = None,
        start_date: Optional[str] = None,
        symbols: Optional[Sequence[str]] = None,
        paths: Optional[Sequence[PathInput]] = None,
    ) -> SessionView:
        """Warm the market only; no run, policy or account events exist yet.

        ``symbols`` (two or more) prepares one experiment on several symbols: from
        ``paths``, one source per symbol (one CSV each), or from ``path`` for every
        symbol (one ITCH file).

        A custom start is ``start_time_ns`` of market time. With ``start_date``
        ('YYYY-MM-DD', price-bar recordings only) ``start_time_ns`` is instead a time of
        day on that date. ``book`` chooses a price-bar recording's synthetic book.
        """
        if start_mode not in ('first_book', 'regular', 'custom', 'beginning'):
            raise ContractError('unknown experiment start mode')
        target = REGULAR_OPEN_NS if start_mode == 'regular' else start_time_ns
        if start_mode == 'custom':
            from sim_platform.market.models import time_ns

            time_ns(target)
        if start_date is not None:
            check_start_date(start_date, start_mode, target)
        universe = None
        if symbols:
            universe = experiment_universe(path, symbols, paths)
            path, symbol = universe[0][1], universe[0][0]
        elif self.can_reuse_preparation(
            path, symbol, news, allow_implicit_eof, start_mode, start_time_ns, book, start_date
        ):
            return self.view()
        self._load_source(
            path, symbol, news, allow_implicit_eof, wait=False, book=book, universe=universe
        )
        self._current_source.update(start_mode=start_mode, start_time_ns=target)
        self.preparation = dict(status='preparing', start_mode=start_mode, target_time_ns=target)
        if start_date is not None:
            # Saved only when given, so earlier runs' recorded sources stay unchanged.
            self._current_source['start_date'] = self.preparation['start_date'] = start_date
        if self._loaded_market.prepare():
            self._position_prepared_source()
        return self.view()

    def _position_prepared_source(self) -> None:
        from sim_platform.market.models import DataUnavailable

        target = self._current_preparation['target_time_ns']
        start_date = self._current_preparation.get('start_date')
        if start_date is not None:
            target = dated_start(self._loaded_market.calendar, start_date, target)
            self._current_preparation['target_time_ns'] = target
        try:
            self._loaded_market.start_position(self._current_preparation['start_mode'], target)
        except DataUnavailable as exc:
            self._current_preparation['status'] = 'failed'
            reason = (
                'dataset ends before the requested start point; no events after it'
                if self._current_preparation['start_mode'] == 'custom'
                else 'dataset ends before the requested start point'
            )
            raise ContractError(reason + ': ' + str(exc)) from exc
        self._current_source['prepared'] = self._loaded_market.prepared_descriptor()
        self._finish_preparation()

    def prepare_tick(
        self,
        *,
        max_events: int = PREPARE_TICK_EVENTS,
        budget_seconds: float = PREPARE_TICK_BUDGET_SECONDS,
    ) -> SessionView:
        """Advance preparation by one market step and return the view."""
        self._check_open()
        if not self.preparation or self.preparation['status'] != 'preparing':
            raise ContractError('no market preparation is active')
        try:
            if self._loaded_market.prepare():
                self._position_prepared_source()
            else:
                self._current_preparation['progress'] = self._loaded_market.preparation_progress()
        except Exception:
            self._current_preparation['status'] = 'failed'
            raise
        return self.view()

    def wait_for_preparation(self) -> SessionView:
        """Wait for the shared preparation path; UI callers should poll prepare_tick instead."""
        from time import sleep

        while self._current_preparation['status'] == 'preparing':
            self.prepare_tick()
            if self._current_preparation['status'] == 'preparing':
                sleep(PREPARE_POLL_SECONDS)
        return self.view()

    def cancel_preparation(self) -> SessionView:
        self._check_open()
        if self.runner is not None:
            raise ContractError('stop the experiment instead of cancelling market preparation')
        if self.market and self.market.status == 'loading':
            self.market.close()
        elif self.market and self.market.status == 'playing':
            self.market.pause()
        self._prepared_origin = None
        self._current_preparation['status'] = 'cancelled'
        self._current_source.pop('start_origin', None)
        return self.view()

    @property
    def policy_controls(self) -> Any:
        self._check_open()
        if self.runner is None:
            raise ContractError('no experiment is bound')
        return self.runner.policy_controls

    @property
    def tool_controls(self) -> Any:
        self._check_open()
        if self.runner is None:
            raise ContractError('no experiment is bound')
        return self.runner.tool_controls

    def _driver(self) -> Union[ResearchRunner, PreparedSession]:
        """The object that advances playback: the runner once bound, else the market."""
        if self.viewed_run or self.market is None:
            raise ContractError('load market data to control playback')
        return self.runner or self.market

    def step(self) -> SessionView:
        """Advance one event; a run that this step finished saves its market data now."""
        self._check_open()
        self._driver().step()
        if self.runner is not None and not self.runner.active:
            self._archive()
        return self.view()

    def play(self, speed: Optional[float] = None) -> SessionView:
        self._check_open()
        self._driver().play(speed)
        return self.view()

    def pause(self) -> SessionView:
        self._check_open()
        self._driver().pause()
        return self.view()

    def tick(
        self, *, render: bool = True, max_events: int = 1, budget_seconds: Optional[float] = None
    ) -> Optional[SessionView]:
        """Poll the driver once and archive a finished run; return the view if ``render``."""
        self._check_open()
        self._driver().poll(max_events=max_events, budget_seconds=budget_seconds)
        if self.runner is not None and not self.runner.active:
            self._archive()
        return self.view() if render else None

    def stop(self) -> SessionView:
        self._check_open()
        if self.runner is not None:
            cleanup(self.runner.stop, self._archive)
        elif self.market and self.market.status == 'playing':
            self.market.pause()
        return self.view()

    def _close_market(self) -> None:
        if self.runner is not None and not self.runner.closed:
            raise ContractError(
                'execution owner has not drained; retain its market for close retry'
            )
        if self.market:
            self.market.close()

    def view_run(self, profile: str, run_id: str) -> SessionView:
        """Release any live experiment and show a saved run's report instead."""
        self._check_open()
        if self._active():
            raise ContractError('stop the active simulation before viewing a saved run')
        self.app.run(profile, run_id, include_records=False)
        if self.runner is not None:
            cleanup(self.runner.close, self._archive, self._close_market)
        else:
            self._close_market()
        self.market = self.runner = None
        self.profile, self.run_id, self.viewed_run = profile, run_id, run_id
        return self.view()

    def recover_stop(self, profile: str, run_id: str) -> SessionView:
        """Mark an interrupted saved run stopped, then view it."""
        self._check_open()
        if self._active():
            raise ContractError('stop the live simulation first')
        self.app.abort_execution_run(profile, run_id)
        return self.view_run(profile, run_id)

    def report(self) -> Report:
        if not self.run_id:
            raise ContractError('no simulation report; start or open a saved run')
        if self.runner is not None and not self.runner.active:
            self._archive()
        # profile is always set together with run_id.
        return run_report(self.app, cast(str, self.profile), self.run_id)

    def export(self, path: PathInput) -> str:
        self._check_open()
        self._archive()
        # Suspected bug: unlike report(), export() does not check that a run is bound, so
        # with no run the None profile and run IDs reach the database layer.
        return export_run(
            self.app,
            self.profile,  # type: ignore[arg-type]
            self.run_id,  # type: ignore[arg-type]
            path,
            writer=self._export_writer,
        )

    @property
    def closed(self) -> bool:
        return self._closed

    def close(self) -> None:
        if self._closed:
            return
        actions: List[Callable[[], None]] = [self.runner.close] if self.runner is not None else []
        actions.extend((self._archive, self._close_market))
        try:
            cleanup(*actions)
        finally:
            if self.runner is None or self.runner.closed:
                self._closed = True
                self._on_close()

    @abstractmethod
    def create_experiment(
        self,
        profile: str,
        account: str,
        *,
        source: PathInput,
        method: str,
        settings: Optional[Dict[str, Any]] = None,
        agent_id: Optional[str] = None,
        revision: Optional[int] = None,
        execution_config: Optional[ExecutionConfig] = None,
        log_path: Optional[PathInput] = None,
        clock: Optional[SharedExperimentClock] = None,
        execution_mode: str,
    ) -> SessionView:
        """Prepare and bind the method supported by this controller subclass."""


def seed_research(
    session: ResearchController,
    demo_source: Optional[Tuple[PathInput, Optional[PathInput]]] = None,
) -> None:
    """Create the demo profile/account and load demo data, or a default profile if none."""
    if demo_source:
        profile = session.create_profile('Demo researcher')
        session.create_account(profile, 'AAPL demo account', DEMO_ACCOUNT_CASH)
        session.load(demo_source[0], news=demo_source[1])
    elif not session.catalog()['profiles']:
        session.create_profile('Default researcher')
