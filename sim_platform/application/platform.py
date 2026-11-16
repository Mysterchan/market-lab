"""Composition root: owns resources; downstream objects implement their functions."""

from __future__ import annotations
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    ContextManager,
    Dict,
    List,
    Optional,
    Sequence,
    Tuple,
    Union,
    cast,
)
from .api_types import (
    ArchiveReceipt,
    ExecutionMode,
    ExperimentMethod,
    JsonObject,
    JsonValue,
    PreparedReplayResult,
    ReplayVerification,
    RunDetails,
)
from sim_platform.domain.models import AccountSnapshot, ContractError, Intent
from pathlib import Path
import logging
from threading import RLock
from sim_platform.persistence.sql.database import Database
from sim_platform.persistence.paths import data_directory
from sim_platform.persistence.workspace.temporary import AppWorkspace
from sim_platform.resources import cleanup, cleanup_after_error
from .operations import DatabaseOperations
from .execution_operations import ApplicationOperations
from .resources import PlatformResources

if TYPE_CHECKING:
    from .experiments.contracts import AgentLaunch
    from sim_platform.sdk import AgentFactory
    from sim_platform.agents.operation_log import AgentOperationLog
    from sim_platform.decisions.agent.runner import AgentDecisionRunner
    from sim_platform.decisions.policy.contracts import Policy
    from sim_platform.experiments.execution.market import LocalMarketExecution
    from sim_platform.decisions.policy.live.runner import LivePolicyDecisionRunner
    from sim_platform.decisions.policy.runner import PolicyDecisionRunner
    from sim_platform.experiments.scheduling.clock import ExperimentClock, SharedExperimentClock
    from sim_platform.experiments.scheduling.host import ExperimentRuntimeHandle
    from sim_platform.experiments.execution.session import ExperimentSession
    from sim_platform.execution.models import ExecutionConfig, ExecutionSnapshot
    from sim_platform.execution.session import ExecutionSession
    from sim_platform.market.playback import MarketPlayback
    from sim_platform.market.prepared.session import PreparedSession
    from sim_platform.research.policy_controller import PolicyResearchController
    from .experiments.contracts import AgentBinding
    from sim_platform.models.credentials import CredentialStore
    from sim_platform.replay.runner import RecoveredRun, ReplaySession
    from sim_platform.research.agent_controller import AgentResearchController
    from sim_platform.research.batch import ResearchBatch
    from sim_platform.research.controller import ResearchController
    from sim_platform.research.datasets import DatasetLibrary
    from sim_platform.research.jobs import BackgroundJob

    MarketInput = Union[MarketPlayback, PreparedSession]

from .platform_agents import PlatformAgents
from .platform_base import PathInput
from .platform_credentials import PlatformCredentials
from .platform_records import PlatformRecords


_log = logging.getLogger(__name__)


class Platform(PlatformRecords, PlatformAgents, PlatformCredentials):
    """Start here to set up, run and inspect local trading experiments.

    A Platform connects the account database, historical market data and experiment
    controls. Create one Platform for your app, then use its methods to create accounts,
    prepare experiments and read results. You do not need to construct its internal
    services yourself.

    For a normal experiment, call open_experiment(). It returns a controller: the object
    you use to play, pause, stop and inspect that experiment. If your screen prepares an
    experiment in several steps, use open_research() instead. Both follow the same setup
    path. Creating a Platform alone does not start an experiment.

    Platform keeps track of the controllers, workers and other resources it creates.
    Use `with Platform(directory) as app` so they are closed before the database closes.
    Methods that return data, such as snapshot(), return a result for that call; reading
    it again later requires another call.

    An ID is a string returned by a creation method. Pass it back unchanged. A profile
    groups a user's accounts and saved agents. A maker identifies who makes decisions
    (a human, rule or agent). A run is the saved record of one experiment's activity.

    Money and prices use integers: 10,000 means USD 1. Quantities are shares. A time named
    *_ns uses nanoseconds on the market session's timeline. Agent tool messages have
    separate rules for counters represented as decimal strings.

    Examples use app for an open Platform and *_id for previously created IDs. Inputs
    such as source_path and agent_settings must be supplied by the caller. Types describe
    the returned Python values; dictionaries remain ordinary dictionaries. Storage,
    market-library and operating-system errors may also reach the caller.
    """

    # Set in __init__; declared here because the storage helpers are defined first.
    directory: Path
    _credentials: Optional[CredentialStore]
    _database_instance: Optional[Database]
    _database_lock: RLock
    _resources: PlatformResources

    @property
    def _database(self) -> Database:
        """Get the database, opening it on first use if initialization was deferred.

        The lock prevents two callers from creating separate database instances. Platform
        keeps the resulting database and closes it during shutdown.

        Returns:
            Database: Existing or newly initialized storage; closed platforms cannot initialize
            it.

        Raises:
            ContractError: If an identity, configuration or lifecycle precondition is invalid.
        """
        with self._database_lock:
            if self._database_instance is None:
                self._resources.check_open()
                self._database_instance = Database(self.directory)
            return self._database_instance

    def _unit_of_work(self, *, write: bool = False) -> ContextManager[Any]:
        """Start a short database transaction for an internal operation.

        The caller uses the returned context manager with a with block. write selects
        whether the transaction can change data; leaving the block commits or rolls it back.

        Args:
            write: Whether the internal operation needs a write transaction.

        Returns:
            ContextManager[Any]: Commits/rolls back at context exit; never retain the ORM
            Session.

        Raises:
            ContractError: If an identity, configuration or lifecycle precondition is invalid.
        """
        return self._database.unit_of_work(write=write)

    def __init__(
        self,
        directory: Optional[PathInput] = None,
        *,
        database: bool = True,
        python_manager: Optional[PathInput] = None,
        agent_sdk_wheel: Optional[PathInput] = None,
    ) -> None:
        """Open the local storage and services that your app will use.

        Call this once before creating accounts or experiments. directory chooses where saved
        data lives. The new Platform is ready to accept calls, but no market or experiment
        has started. A temporary workspace is also created for work files.

        With database=True, the SQLite database opens now. With database=False, it opens on
        the first database operation; saved data is still enabled. Use a with block to close
        experiments and workers before their storage is closed.

        Args:
            directory: Storage directory; None selects the per-OS-user default. Explicit paths
                must be absolute.
            database: Open SQLite immediately when True; otherwise initialize on first database
                use.
            python_manager: Trusted uv executable bundled with the App. None uses uv from PATH
                for repository use; never resolved from Agent source.
            agent_sdk_wheel: Optional trusted SDK wheel bundled by the App. Repository use
                builds a wheel from the explicit client-only inventory when omitted.

        Returns:
            None. The instance starts open, with no research controller or run created.

        Raises:
            ContractError: If directory is not absolute.
            OSError: If storage cannot be opened; database initialization errors propagate.

        Example:
            from tempfile import TemporaryDirectory
            with TemporaryDirectory() as folder:
                with Platform(folder) as app:
                    profile_id = app.create_profile('Research')
        """
        self.directory = Path(directory).expanduser() if directory is not None else data_directory()
        if not self.directory.is_absolute():
            raise ContractError('data directory must be absolute')
        self._credentials = None
        self._database_instance = None
        self._database_lock = RLock()
        self._workspace = AppWorkspace()
        self._resources = PlatformResources()
        from sim_platform.execution.coordinator import ExecutionCoordinator
        from sim_platform.persistence.workspace.ownership import AccountLease
        from .replay_manifest import capture_source
        from sim_platform.persistence.export_files import atomic_write
        from sim_platform.persistence.datasets.files import DatasetFiles

        self._export_writer = atomic_write
        self._dataset_files = DatasetFiles(self.directory)
        self._db_operations = DatabaseOperations(self._unit_of_work)
        execution = ExecutionCoordinator(
            self._db_operations,
            self._resources,
            source_identity=capture_source,
            lease_factory=lambda account: AccountLease(self.directory, account),
            lineage=self._record_lineage,
        )
        self._operations = ApplicationOperations(self._db_operations, execution)
        from sim_platform.persistence.lineage import LineageFiles
        from .lineage import RunLineage

        self._lineage = RunLineage(
            LineageFiles(self.directory),
            run=self._operations.run,
            events=self._operations.run_events,
            decisions=self._operations.decision_records,
            agent_log=self._operations.agent_log_archive,
        )
        from sim_platform.persistence.agents.projects import ProjectFiles
        from sim_platform.persistence.agents.environments import EnvironmentFiles
        from .agents.projects import AgentProjects

        self._agent_project_files = ProjectFiles(self.directory)
        self._agent_projects = AgentProjects(self._operations, self._agent_project_files)
        from sim_platform.agents.environments.installer import environment_python

        self._agent_environment_files = EnvironmentFiles(
            self.directory, python_path=environment_python
        )
        from sim_platform.persistence.agents.sdk import SDKFiles

        self._agent_sdk = SDKFiles(self.directory, agent_sdk_wheel)
        from sim_platform.persistence.agents.runtimes import RuntimeFiles
        from sim_platform.agents.runtimes.provider import UvPythonProvider
        from sim_platform.agents.runtimes.manager import PythonRuntimes

        self._python_runtimes = PythonRuntimes(
            RuntimeFiles(self.directory), UvPythonProvider(python_manager)
        )
        from .agents.environments import AgentEnvironments

        self._agent_environments = AgentEnvironments(
            self._agent_projects,
            self._agent_environment_files,
            self._python_runtimes,
            self._agent_sdk,
        )
        if database:
            try:
                self._database_instance = Database(self.directory)
            except Exception as exc:
                cleanup_after_error(exc, self._workspace.close)
                raise

    @property
    def closed(self) -> bool:
        """Check whether Platform shutdown has started.

        Use this when deciding whether the app can still create resources. True means new
        resources are no longer accepted. It does not guarantee that every worker has exited:
        if close() raised an error, fix that problem and call close() again.

        Returns:
            bool: False initially; True once close() begins, including failed cleanup.

        Example:
            is_closed = app.closed
        """
        return self._resources.closed

    def _new_market(
        self, symbol: str = 'AAPL', *, depth: int = 5, library: Optional[PathInput] = None
    ) -> MarketPlayback:
        """Build a market playback object without adding it to Platform cleanup yet.

        The object has temporary history storage but no loaded source. Its caller must
        either register it for cleanup or attach it to a parent that will close it.

        Args:
            symbol: Stock symbol, such as AAPL. Trading experiments currently use one
                symbol per run; standalone playback selects the book for this symbol.
            depth: Retained/displayed book levels, from 1 to 100.
            library: Path to the compiled market-data library. Usually omit this to use
                the configured or bundled library.

        Returns:
            MarketPlayback: Unloaded child; caller must register it or close it on assembly
            failure.

        Raises:
            ContractError: If an identity, configuration or lifecycle precondition is invalid.
        """
        self._resources.check_open()
        from sim_platform.market import MarketSession
        from sim_platform.market.playback import MarketPlayback
        from sim_platform.market.history import MarketHistory

        engine = MarketSession(symbol, depth=depth, library=library)
        try:
            files = self._workspace.market_cache()
        except Exception as exc:
            cleanup_after_error(exc, engine.close)
            raise
        try:
            return MarketPlayback(engine, MarketHistory(files))
        except Exception as exc:
            cleanup_after_error(exc, engine.close, files.close)
            raise

    def _new_research_market(
        self,
        symbol: str = 'AAPL',
        *,
        depth: int = 5,
        library: Optional[PathInput] = None,
        bar_spread: Optional[int] = None,
        bar_origin: Optional[str] = None,
    ) -> PreparedSession:
        """Create the market reader used while preparing a research experiment.

        It uses the local dataset inventory to find or build prepared historical data.
        The caller attaches the reader to its controller and takes responsibility for closing it.

        Args:
            symbol: Stock symbol, such as AAPL. Trading experiments currently use one
                symbol per run; standalone playback selects the book for this symbol.
            depth: Retained/displayed book levels, from 1 to 100.
            library: Path to the compiled market-data library. Usually omit this to use
                the configured or bundled library.
            bar_spread: A price-bar recording's synthetic spread in integer money; None
                keeps the default of two cents.
            bar_origin: A price-bar recording's market day 0 ('YYYY-MM-DD'), when it
                shares a timeline with recordings that start earlier.

        Returns:
            PreparedSession: Unloaded child; no import job starts until load().

        Raises:
            ContractError: If an identity, configuration or lifecycle precondition is invalid.
        """
        from sim_platform.market.prepared.session import PreparedSession
        from sim_platform.persistence.prepared.store import PreparedStore

        self._resources.check_open()
        return PreparedSession(
            PreparedStore(self.directory),
            symbol,
            depth=depth,
            library=library,
            bar_spread=bar_spread,
            bar_origin=bar_origin,
        )

    def open_market(
        self, symbol: str = 'AAPL', *, depth: int = 5, library: Optional[PathInput] = None
    ) -> MarketPlayback:
        """Create a market reader that you can load and advance yourself.

        Use this to inspect or replay a market file without setting up a trading experiment.
        The returned MarketPlayback starts without a file. Call load() to select data, then
        step() or play() to move through it.

        Platform closes this object and its temporary history files when Platform closes.
        You can close it earlier. For trading experiments, use open_experiment(), which
        prepares its own market data and connects it to the account and decision runner.

        Args:
            symbol: Stock symbol, such as AAPL. Trading experiments currently use one
                symbol per run; standalone playback selects the book for this symbol.
            depth: Retained/displayed book levels, from 1 to 100.
            library: Path to the compiled market-data library. Usually omit this to use
                the configured or bundled library.

        Returns:
            MarketPlayback: An open, unloaded source; no account or execution run is created.

        Raises:
            ContractError: If the platform is closed.
            MarketError: If symbol/depth is invalid.
            OSError: If the native library cannot be loaded.
            RuntimeError: If a native engine cannot be allocated.

        Example:
            market = app.open_market('AAPL', depth=5)
            market.load(source_path)
            event = market.step()
            market.close()
        """
        market = self._new_market(symbol, depth=depth, library=library)
        market.on_close(lambda: self._resources.remove(market))
        return self._resources.add(market)

    def open_jobs(self) -> BackgroundJob:
        """Create a worker for running one slow task away from your calling thread.

        This is useful when a screen needs to stay responsive during preparation. Creating
        the worker does not start a task. Call start(operation, **arguments), then check its
        progress and collect the result with take_result(). The operation receives a
        cancellation token and must check it if it needs to support cancellation.

        Platform will close the worker. You can also close it yourself, but close() waits
        for the task to finish or cooperate with cancellation.

        Returns:
            BackgroundJob: No task submitted; active is False.

        Raises:
            ContractError: If an identity, configuration or lifecycle precondition is invalid.

        Example:
            jobs = app.open_jobs()
            jobs.start(operation, source=source_path)
            # Poll jobs.active / take_result() from your own event loop.
            jobs.close()
        """
        self._resources.check_open()
        from sim_platform.research.jobs import BackgroundJob

        job = BackgroundJob()
        job.on_close(lambda: self._resources.remove(job))
        return self._resources.add(job, shutdown_first=True)

    def _new_execution(
        self, profile: str, run_id: str, maker: str, market: MarketInput
    ) -> ExecutionSession:
        """Connect a run, account and market to one order-execution session.

        This internal factory shares Platform's account-ownership checks, so two executors
        cannot write the same account at once. It does not register the session as a root
        resource: the caller must close it or give that responsibility to its runner.

        Args:
            profile: Profile ID selecting whose saved data and account to use.
            run_id: Existing run ID owned by the selected profile.
            maker: Maker ID with current command authority for the run.
            market: Loaded market borrowed from the caller; it must match the run/source prefix.

        Returns:
            ExecutionSession: Open account writer; caller owns close, market is borrowed.

        Raises:
            ContractError: If an identity, configuration or lifecycle precondition is invalid.
        """
        self._resources.check_open()
        from sim_platform.execution import ExecutionSession

        return ExecutionSession(
            self._operations, profile, run_id, maker, market, ownership=self._resources
        )

    def _new_runner(
        self, profile: str, run_id: str, maker: str, market: MarketInput, policy: Policy
    ) -> PolicyDecisionRunner:
        """Connect a policy to a market and its order-execution session.

        This internal factory builds the synchronous runner used for controlled decisions.
        The runner shares the platform's financial command path. Its parent must close the
        runner, while the market remains borrowed from the caller.

        Args:
            profile: Profile ID selecting whose saved data and account to use.
            run_id: Existing run ID owned by the selected profile.
            maker: Maker ID with current command authority for the run.
            market: Loaded market borrowed from the caller; it must match the run/source prefix.
            policy: Strategy object implementing decisions.contracts.Policy. For an
                existing run, its identity and settings must match the saved policy.

        Returns:
            PolicyDecisionRunner: Bound child; caller owns cleanup and borrowed market lifetime.

        Raises:
            ContractError: If an identity, configuration or lifecycle precondition is invalid.
        """
        self._resources.check_open()
        from sim_platform.decisions import PolicyDecisionRunner
        from sim_platform.experiments.execution.market import ExecutionCommands

        return PolicyDecisionRunner(
            self._operations,
            profile,
            run_id,
            maker,
            market,
            policy,
            execution_factory=self._new_execution,
            commands=ExecutionCommands(self._operations.commit_decision, profile, run_id),
        )

    def _new_live_policy_runner(
        self, profile: str, run_id: str, maker: str, market: MarketInput, policy: Policy
    ) -> LivePolicyDecisionRunner:
        """Build the policy runner used when an experiment runs in the background.

        It connects policy decisions to the background execution session and its output
        recording. The controller receives a ready runner and starts it later with Play.
        The parent takes responsibility for closing it.

        Args:
            profile: Profile ID selecting whose saved data and account to use.
            run_id: Existing run ID owned by the selected profile.
            maker: Maker ID with current command authority for the run.
            market: Loaded market borrowed from the caller; it must match the run/source prefix.
            policy: Strategy object implementing decisions.contracts.Policy. For an
                existing run, its identity and settings must match the saved policy.

        Returns:
            LivePolicyDecisionRunner: Ready child; no Play until the Controller requests it.

        Raises:
            ContractError: If an identity, configuration or lifecycle precondition is invalid.
        """
        from .experiments.factory import assemble_live_policy

        return assemble_live_policy(
            self._new_runner,
            self.directory,
            profile,
            run_id,
            maker,
            # Live experiments always run on a prepared source.
            cast('PreparedSession', market),
            policy,
            pipeline_factory=self._compose_pipeline,
        )

    def _new_experiment_runtime(
        self,
        configuration: JsonObject,
        *,
        clock: Optional[SharedExperimentClock] = None,
        on_notice: Optional[Callable[[str, JsonObject], None]] = None,
        on_failure: Optional[Callable[[Exception], None]] = None,
        prepared_artifact: Optional[PathInput] = None,
        prepared_news: Sequence[JsonObject] = (),
    ) -> ExperimentRuntimeHandle:
        """Start the separate process that schedules Agent work and model attempts.

        Use this while assembling an Agent runner. Check that Platform is open, then
        pass the validated run settings and trusted market source to Runtime assembly.
        This call waits for child initialization; it does not start the experiment clock.

        The returned handle is not registered as a Platform root. The caller must give
        it to the runner or close it if the remaining assembly fails. Model requests
        and financial execution remain owned by their respective components.

        Args:
            configuration: Complete Agent run configuration, containing references rather
                than credentials. Session start/end values are nanoseconds encoded as strings.
            clock: Shared experiment clock, or None to let Runtime create its default clock.
            on_notice: Optional nonblocking callback receiving an event name and detached
                details from Runtime.
            on_failure: Optional nonblocking callback receiving a Runtime transport or
                child-process failure.
            prepared_artifact: Trusted immutable market directory. Required for assembly;
                its path is supplied to Runtime, never to the Agent program.
            prepared_news: Publication records merged into Runtime's market observation
                index. An empty sequence adds no publications.

        Returns:
            ExperimentRuntimeHandle: Initialized scheduling child and transport. The caller
                owns close until ownership transfers to the assembled runner.

        Raises:
            ContractError: If Platform is closed, prepared_artifact is missing, the clock
                or capacity is unsupported, or the child does not become ready.
            AgentConfigurationError: If the supplied run configuration is invalid.
            RuntimeError: If a Runtime transport thread cannot start.
            OSError: If operating-system resources for the child cannot be created.
        """
        from .experiments.runtime import assemble_runtime

        self._resources.check_open()
        return assemble_runtime(
            configuration,
            clock=clock,
            on_notice=on_notice,
            on_failure=on_failure,
            prepared_artifact=prepared_artifact,
            prepared_news=prepared_news,
        )

    def run(self, profile_id: str, run_id: str, *, include_records: bool = True) -> RunDetails:
        """Read the saved record of one experiment run.

        Use this to find its account, decision-maker, configuration and saved status. It
        returns data, not an object that controls the experiment. Reading it does not ask
        the strategy to make a decision or move the market forward.

        The database status 'running' means the run has not ended. It also covers the time
        before Play and while paused. Use the experiment controller to check playback state.
        Set include_records=False when you do not need the full decision history; the result
        may then come from the committed account data already held in memory.

        Args:
            profile_id: Profile ID returned by create_profile(); selects whose data to access.
            run_id: Existing run ID owned by the selected profile.
            include_records: Include the full policy decision record journal when True.

        Returns:
            RunDetails: A dictionary describing this run, including status, time_ns, config, initial_snapshot,
            policy_state and decision_maker.

        Raises:
            ContractError: If an identity, configuration or lifecycle precondition is invalid.

        Example:
            details = app.run(profile_id, run_id, include_records=False)
            status = details['status']
        """
        return self._operations.run(profile_id, run_id, include_records=include_records)

    def execution_session(
        self, profile_id: str, run_id: str, maker_id: str, market: MarketInput
    ) -> ExecutionSession:
        """Connect an existing execution run to the market it will trade against.

        This is for callers assembling an experiment themselves. First create_execution_run()
        saves the run; this method gives it an ExecutionSession that processes market events
        and order operations for that run. Setup checks the run, its account and market
        position, and prevents another executor from changing the same account at once.

        You keep responsibility for the market passed in. Close the session before closing
        that market; Platform also tracks and closes the session. Runs configured with a
        policy use decision_runner() instead. Most callers should use open_experiment() to
        have these parts connected for them.

        Args:
            profile_id: Profile ID returned by create_profile(); selects whose data to access.
            run_id: Existing run ID owned by the selected profile.
            maker_id: Maker ID with current command authority for the run.
            market: Loaded market borrowed from the caller; it must match the run/source prefix.

        Returns:
            ExecutionSession: Ready to process this run, with its account and orders loaded
            into memory. Market playback has not started.

        Raises:
            ContractError: If an identity, configuration or lifecycle precondition is invalid.

        Example:
            session = app.execution_session(profile_id, run_id, maker_id, market)
            event = session.step()
            session.close()
        """
        if 'decision' in self.run(profile_id, run_id, include_records=False)['config']:
            raise ContractError('bind decision runs through DecisionRunner')
        owner = self._new_execution(profile_id, run_id, maker_id, market)
        owner.on_close(lambda: self._resources.remove(owner))
        return self._resources.add(owner)

    def archive_agent_log(self, profile_id: str, run_id: str, path: PathInput) -> ArchiveReceipt:
        """Save a finished agent's local JSON activity log in the run's database archive.

        Use this after the run has ended and its log has been sealed, meaning no more records
        can be added. The file is read and checked before the database write. It must belong
        to the requested profile, experiment and run.

        Saving the same content again is allowed; replacing it with different content is
        rejected. The original JSON file stays on disk. This does not execute its tool calls.

        Args:
            profile_id: Profile ID returned by create_profile(); selects whose data to access.
            run_id: Existing run ID owned by the selected profile.
            path: Absolute path to a sealed JSON operation log.

        Returns:
            ArchiveReceipt: run_id and record_count; no new run or trade is created.

        Raises:
            ContractError: If the log is incomplete, mismatched, conflicting or the run is active.
            OSError: If the log cannot be read.

        Example:
            receipt = app.archive_agent_log(profile_id, run_id, log_path)
            count = receipt['record_count']
        """
        from sim_platform.agents.operation_log import validate_archive
        from sim_platform.persistence.agent_log_files import read_agent_log

        archive = validate_archive(read_agent_log(path))
        receipt = self._operations.archive_agent_log(profile_id, run_id, archive)
        try:
            self._lineage.record_agent(profile_id, run_id)
        except Exception:
            _log.warning('Could not record Agent lineage of run %s', run_id, exc_info=True)
        return receipt

    def _compose_pipeline(
        self,
        driver: LocalMarketExecution,
        market: PreparedSession,
        *,
        end_ns: int,
        sink: Callable[[JsonObject], None],
        operation: Callable[[Any, Dict[str, str]], Any],
        lag_limit_ns: int,
        clock: Optional[Union[ExperimentClock, SharedExperimentClock]] = None,
        start_ns: Optional[int] = None,
        capacity: int = 64,
        lag_window_ns: int = 0,
    ) -> ExperimentSession:
        """Connect a prepared market, clock and financial commands for background execution.

        Use this while assembling an Agent or Live policy runner. Pipeline assembly
        captures the initial account and execution configuration for recorded Replay,
        and binds the shared event reader used to collect terminal financial evidence.

        The output stage is created synchronously. The financial execution worker starts
        only when the returned Session is started. The caller must attach the Session to
        its runner or close it on assembly failure; driver and market remain borrowed.

        Args:
            driver: Owner-only financial execution capability borrowing the existing
                execution session and market cursor.
            market: Loaded PreparedSession matching the run and its source prefix.
            end_ns: Exclusive end in session nanoseconds, clipped to the source coverage.
            sink: Output-stage callback receiving ordered, detached JSON records.
            operation: Trusted short handler receiving a command and its ordered position
                on the sole financial owner.
            lag_limit_ns: Financial processing delay threshold, in nanoseconds.
            clock: Experiment clock, or None to create one at the selected start point.
            start_ns: Start in session nanoseconds, or None to use market.origin_ns.
            capacity: Maximum pending/result slots; defaults to 64. Exhaustion is rejected
                rather than creating an unbounded queue.
            lag_window_ns: Allowed continuous threshold-exceedance duration, in nanoseconds;
                defaults to zero.

        Returns:
            ExperimentSession: Ready background lifecycle with Replay inputs and terminal
                evidence bound. The financial worker has not started.

        Raises:
            ContractError: If market is not prepared or the selected interval exceeds
                the prepared source coverage.
            OSError: If required operating-system resources cannot be created.
            RuntimeError: If the output stage cannot start.
        """
        from .experiments.pipeline import assemble_pipeline

        return assemble_pipeline(
            driver,
            market,
            run_events=self._operations.run_events,
            end_ns=end_ns,
            sink=sink,
            operation=operation,
            lag_limit_ns=lag_limit_ns,
            clock=clock,
            start_ns=start_ns,
            capacity=capacity,
            lag_window_ns=lag_window_ns,
        )

    def _new_agent_log(
        self, profile_id: str, experiment_id: str, run_id: str, path: PathInput
    ) -> AgentOperationLog:
        """Create the local activity recorder for an agent run being assembled.

        First check that the saved run belongs to the requested agent experiment. Then open
        the JSON log at its chosen path. The caller must attach the recorder to the runner
        or close it if the remaining setup fails.

        Args:
            profile_id: Profile ID returned by create_profile(); selects whose data to access.
            experiment_id: Saved Agent experiment draft ID owned by the profile.
            run_id: Existing run ID owned by the selected profile.
            path: Absolute local file path; see lifecycle requirements below.

        Returns:
            AgentOperationLog: Open unsealed file; caller must close it on rollback.

        Raises:
            ContractError: If an identity, configuration or lifecycle precondition is invalid.
        """
        from sim_platform.agents.operation_log import AgentOperationLog
        from sim_platform.persistence.agent_log_files import AgentLogFile

        self._resources.check_open()
        header = self._operations.agent_log_header(profile_id, experiment_id, run_id)
        adapter = AgentLogFile(path)
        try:
            recorder = AgentOperationLog(adapter, header)
        except Exception as exc:
            cleanup_after_error(exc, adapter.close)
            raise
        return recorder

    def create_agent_log(
        self, profile_id: str, experiment_id: str, run_id: str, path: PathInput
    ) -> AgentOperationLog:
        """Create a local JSON file for recording an agent run's activity.

        This is a manual setup API. The run must already exist and be connected to its agent
        experiment. The path must be absolute and unused. Normal experiment setup creates
        its own recorder, so most callers do not need this method.

        The returned recorder writes to a file as the run proceeds. Platform closes it during
        shutdown, but closing alone does not mark the log complete or save it to the database.
        Seal the completed log before calling archive_agent_log().

        Args:
            profile_id: Profile ID returned by create_profile(); selects whose data to access.
            experiment_id: Saved Agent experiment draft ID owned by the profile.
            run_id: Existing run ID owned by the selected profile.
            path: Absolute path for a new file; existing files are never overwritten.

        Returns:
            AgentOperationLog: Open recorder with a header written; the log is not sealed.

        Raises:
            ContractError: If bindings or path are invalid.
            OSError: If the file exists or cannot be created.

        Example:
            recorder = app.create_agent_log(profile_id, experiment_id, run_id, new_log_path)
            # Record completed operations and finish with the actual terminal status.
            recorder.close()
        """
        recorder = self._new_agent_log(profile_id, experiment_id, run_id, path)
        recorder.on_close(lambda: self._resources.remove(recorder))
        return self._resources.add(recorder)

    def _cancel_failed_agent_start(self, profile: str, run: str) -> None:
        """Finish cleanup when an agent cannot be assembled after its run was saved.

        First try to stop a run that has not acquired an executor. If that is no longer
        possible, use the normal execution recovery path. This prevents setup failure from leaving an active run
        that blocks later experiments on the account.

        Args:
            profile: Profile ID selecting whose saved data and account to use.
            run: Existing owned run ID.

        Returns:
            None. An active unbound run is stopped, or the financial recovery finish path is
            used.

        Raises:
            ContractError: If an identity, configuration or lifecycle precondition is invalid.
        """
        if self._operations.run(profile, run, include_records=False)['status'] == 'running':
            if not self._operations.cancel_unbound_agent_run(profile, run):
                self._operations.abort_execution_run(profile, run)

    def _agent_project_launch(self, profile: str, definition: JsonObject) -> AgentLaunch:
        """Resolve a saved Agent revision and its ready environment for process launch.

        Use this before constructing the Agent process host. Check ownership and the
        saved project reference, find the compatible prepared environment, then copy the
        saved source into a temporary workspace. This performs local file work synchronously;
        it does not install dependencies or execute Agent code.

        The caller owns the returned workspace until it transfers the launch to the process
        host. If host assembly cannot take ownership, the caller must close the workspace.

        Args:
            profile: Profile ID owning the saved Agent revision and source snapshot.
            definition: Validated saved Agent definition containing agent_id, revision
                and package.project_ref. The exact revision is selected for launch.

        Returns:
            AgentLaunch: Executable and project-directory paths, plus the owned temporary
                source workspace. Pass this launch to the process host during assembly.

        Raises:
            ContractError: If Platform is closed, ownership or the project reference is
                invalid, or no compatible environment is ready. Prepare the environment
                before retrying launch.
            OSError: If saved source, environment files or the temporary workspace cannot
                be read or created.
        """
        self._resources.check_open()
        return self._agent_environments.launch(profile, definition, self._workspace)

    def _assemble_agent_runner(
        self,
        profile: str,
        binding: JsonObject,
        market: PreparedSession,
        log_path: Optional[PathInput],
        clock: Optional[SharedExperimentClock] = None,
        *,
        execution_mode: ExecutionMode,
        agent_factory: Optional[AgentFactory] = None,
    ) -> AgentDecisionRunner:
        """Give the agent assembly code the Platform factories it needs.

        The assembly code connects execution, scheduling, agent tools and JSON recording
        into one AgentDecisionRunner. Passing these factories keeps it on the same account,
        command and cleanup paths as the rest of Platform.

        Args:
            agent_factory: Optional trusted, spawn-picklable create_agent(context) callable.
                Advanced Python composition hook. Normal callers omit it: configured Agents
                use the template; imported Agents load their saved source and ready environment.
            profile: Profile ID selecting whose saved data and account to use.
            binding: Detached run/maker/experiment/configuration binding returned by the run
                service.
            market: Loaded market borrowed from the caller; it must match the run/source prefix.
            log_path: Absolute path for a new JSON activity log. None chooses the default
                local path for this run.
            clock: Optional trusted shared experiment clock for test/advanced composition;
                normal callers omit it.
            execution_mode: Required playback mode: controlled for caller-driven policy
                execution, live for background execution. Agent currently uses
                background 1x in either mode.

        Returns:
            AgentDecisionRunner: Ready owned children; registration remains the caller's
            responsibility.

        Raises:
            ContractError: If an identity, configuration or lifecycle precondition is invalid.
        """
        from .experiments.agent import assemble_agent_runner
        from .experiments.contracts import AgentAssemblyServices

        return assemble_agent_runner(
            profile,
            cast('AgentBinding', binding),
            market,
            log_path,
            clock,
            services=AgentAssemblyServices(
                runtime_factory=self._new_experiment_runtime,
                execution_factory=self._new_execution,
                compose_pipeline=self._compose_pipeline,
                directory=self.directory,
                create_log=self._new_agent_log,
                archive_log=self.archive_agent_log,
                commit_decision=self._operations.commit_decision,
                failed_start=self._cancel_failed_agent_start,
                resolve_project=self._agent_project_launch,
                presentation_operations=self._operations,
            ),
            agent_factory=agent_factory,
        )

    def _validate_agent_market(self, market: PreparedSession, source: JsonObject) -> JsonObject:
        """Check that the supplied market is at the agent experiment's intended start.

        The market must be ready or paused. Its symbol, event sequence and time must match
        the supplied source description, and all events through the chosen start must have
        been processed. This also prepares the market history used by agent reads and
        returns a separate copy of the source description.

        Args:
            market: Loaded market borrowed from the caller; it must match the run/source prefix.
            source: Detached source identity/start_origin metadata supplied by the trusted
                caller.

        Returns:
            JsonObject: Detached source metadata; the borrowed market's Agent history capability
            is prepared.

        Raises:
            ContractError: If an identity, configuration or lifecycle precondition is invalid.
        """
        from sim_platform.domain.json_values import copy_json

        source = copy_json(source)
        origin = dict(sequence=market.snapshot.sequence, time_ns=market.time_ns)
        if (
            market.status not in ('ready', 'paused')
            or not isinstance(source, dict)
            or source.get('start_origin') != origin
            or source.get('symbol') not in getattr(market, 'universe', (market.snapshot.symbol,))
            or not isinstance(source.get('source_manifest_ref'), str)
        ):
            raise ContractError('supply a ready prepared market with matching source metadata')
        start = source.get('experiment_start_ns', market.time_ns)
        if (
            type(start) is not int
            or start < market.time_ns
            or (market.next_event_time_ns is not None and market.next_event_time_ns <= start)
        ):
            raise ContractError('market prefix is not fully prepared through experiment start')
        market.prepare_agent_history()
        return source

    def _new_agent_run(
        self,
        profile: str,
        experiment_id: str,
        market: PreparedSession,
        *,
        source: JsonObject,
        log_path: Optional[PathInput],
        clock: Optional[SharedExperimentClock] = None,
        execution_mode: ExecutionMode,
        agent_factory: Optional[AgentFactory] = None,
    ) -> AgentDecisionRunner:
        """Create an actual run from a saved agent experiment draft.

        After checking the market and execution mode, save the run and assemble its agent
        runner. The draft is saved configuration; the new run records this execution.
        The parent controller must close the returned runner.

        Args:
            agent_factory: Optional trusted, spawn-picklable create_agent(context) callable.
                Advanced Python composition hook. Normal callers omit it: configured Agents
                use the template; imported Agents load their saved source and ready environment.
            profile: Profile ID selecting whose saved data and account to use.
            experiment_id: Saved Agent experiment draft ID owned by the profile.
            market: Loaded market borrowed from the caller; it must match the run/source prefix.
            source: Detached prepared-source metadata matching the saved draft and initial
                account.
            log_path: Absolute path for a new JSON activity log. None chooses the default
                local path for this run.
            clock: Optional trusted shared experiment clock for test/advanced composition;
                normal callers omit it.
            execution_mode: Required playback mode: controlled for caller-driven policy
                execution, live for background execution. Agent currently uses
                background 1x in either mode.

        Returns:
            AgentDecisionRunner: Ready child; source/account bindings checked before assembly.

        Raises:
            ContractError: If an identity, configuration or lifecycle precondition is invalid.
        """
        self._resources.check_open()
        from sim_platform.domain.experiment import validate_execution_mode

        source = dict(
            self._validate_agent_market(market, source),
            execution_mode=validate_execution_mode(execution_mode),
        )
        binding = self._operations.create_agent_run(profile, experiment_id, source)
        return self._assemble_agent_runner(
            profile,
            binding,
            market,
            log_path,
            clock,
            execution_mode=execution_mode,
            agent_factory=agent_factory,
        )

    def _new_agent_experiment_run(
        self,
        profile: str,
        account: str,
        agent_id: str,
        revision: int,
        market: PreparedSession,
        settings: JsonObject,
        source: JsonObject,
        execution_config: Optional[ExecutionConfig] = None,
        log_path: Optional[PathInput] = None,
        clock: Optional[SharedExperimentClock] = None,
        *,
        execution_mode: ExecutionMode,
        agent_factory: Optional[AgentFactory] = None,
    ) -> AgentDecisionRunner:
        """Save the agent experiment choices and its run, then connect the runner.

        The draft and run are saved together before process assembly. If assembly fails,
        the failed-start cleanup handles the created run. This is the internal setup path
        used when the caller supplies all experiment choices at once.

        Args:
            agent_factory: Optional trusted, spawn-picklable create_agent(context) callable.
                Advanced Python composition hook. Normal callers omit it: configured Agents
                use the template; imported Agents load their saved source and ready environment.
            profile: Profile ID selecting whose saved data and account to use.
            account: Account ID to trade with; the account must belong to profile.
            agent_id: Registered Agent definition ID, not a maker or run ID.
            revision: Positive saved Agent revision number.
            market: Loaded market borrowed from the caller; it must match the run/source prefix.
            settings: Caller-owned experiment settings; policy methods allow defaults, Agent
                requires a complete settings object.
            source: Detached prepared-source metadata matching the selected settings and market.
            execution_config: Rules for order validation and fills. None uses the default
                ExecutionConfig.
            log_path: Absolute path for a new JSON activity log. None chooses the default
                local path for this run.
            clock: Optional trusted shared experiment clock for test/advanced composition;
                normal callers omit it.
            execution_mode: Required playback mode: controlled for caller-driven policy
                execution, live for background execution. Agent currently uses
                background 1x in either mode.

        Returns:
            AgentDecisionRunner: Ready child; setup failures compensate the created run.

        Raises:
            ContractError: If an identity, configuration or lifecycle precondition is invalid.
        """
        self._resources.check_open()
        from sim_platform.domain.experiment import validate_execution_mode

        source = dict(
            self._validate_agent_market(market, source),
            execution_mode=validate_execution_mode(execution_mode),
        )
        binding = self._operations.create_agent_experiment_run(
            profile, account, agent_id, revision, settings, source, execution_config
        )
        return self._assemble_agent_runner(
            profile,
            binding,
            market,
            log_path,
            clock,
            execution_mode=execution_mode,
            agent_factory=agent_factory,
        )

    def open_agent_run(
        self,
        profile: str,
        experiment_id: str,
        market: PreparedSession,
        *,
        source: JsonObject,
        log_path: Optional[PathInput] = None,
        clock: Optional[SharedExperimentClock] = None,
        execution_mode: ExecutionMode,
        agent_factory: Optional[AgentFactory] = None,
    ) -> AgentDecisionRunner:
        """Turn a saved agent experiment draft into a ready decision runner.

        Use this only when you have already called prepare_agent_experiment() and prepared
        the market yourself. The draft chooses the agent, account and settings. This method
        checks that the account still matches the draft and that source describes the market
        and its starting position, then creates the run and connects the agent components.

        Agent, Tools and scheduling processes are prepared during this call. The returned
        runner is ready, but playback has not started. Agent runs currently use background
        1x playback in either execution mode. Platform closes the runner; you must keep the
        supplied market open until the runner closes. For complete setup use open_experiment().

        Args:
            agent_factory: Optional trusted, spawn-picklable create_agent(context) callable.
                Advanced Python composition hook. Normal callers omit it: configured Agents
                use the template; imported Agents load their saved source and ready environment.
            profile: Profile ID selecting whose saved data and account to use.
            experiment_id: Saved Agent experiment draft ID owned by the profile.
            market: Loaded market borrowed from the caller; it must match the run/source prefix.
            source: Detached matching metadata: symbol, source_manifest_ref, start_origin
                (sequence/time_ns), prepared descriptor and optional experiment_start_ns.
            log_path: Absolute path for a new JSON activity log. None chooses the default
                local path for this run.
            clock: Optional trusted shared experiment clock for test/advanced composition;
                normal callers omit it.
            execution_mode: Required playback mode: controlled for caller-driven policy
                execution, live for background execution. Agent currently uses
                background 1x in either mode.

        Returns:
            AgentDecisionRunner: Ready background runner; pipeline state is ready and Play has
            not started.

        Raises:
            ContractError: If an identity, configuration or lifecycle precondition is invalid.

        Example:
            runner = app.open_agent_run(profile_id, draft_id, market,
                source=source_metadata, execution_mode='live')
            runner.play()
            runner.close()
        """
        runner = self._new_agent_run(
            profile,
            experiment_id,
            market,
            source=source,
            log_path=log_path,
            clock=clock,
            execution_mode=execution_mode,
            agent_factory=agent_factory,
        )
        runner.on_close(lambda: self._resources.remove(runner))
        return self._resources.add(runner, shutdown_first=True)

    def open_research(
        self,
        *,
        method: ExperimentMethod = 'human',
        library: Optional[PathInput] = None,
        agent_factory: Optional[AgentFactory] = None,
    ) -> ResearchController:
        """Create an empty workspace for preparing and controlling an experiment.

        Use this when your UI lets someone choose data and settings in separate steps. The
        returned ResearchController holds those choices and later controls the experiment.
        At this point it has no market, run or decision runner, and no child process starts.

        Call create_experiment() to prepare everything in one call, or use the controller's
        preparation methods to show progress. Choose execution_mode when creating the
        experiment. If you already have all the inputs, open_experiment() performs these
        same steps for you. Platform tracks the controller and closes it during shutdown.

        Args:
            agent_factory: Optional trusted, spawn-picklable create_agent(context) callable.
                Advanced Python composition hook. Normal callers omit it: configured Agents
                use the template; imported Agents load their saved source and ready environment.
            method: Who makes decisions: human for manual input, rule for the built-in rule
                policy, hold for the hold policy, model for a user's model file (Controlled
                mode only; see docs/user-guide/model-policies.md), or agent for a saved
                agent definition.
            library: Path to the compiled market-data library. Usually omit this to use
                the configured or bundled library.

        Returns:
            ResearchController: AgentResearchController for agent, otherwise
            PolicyResearchController. market, runner and run_id are None.

        Raises:
            ContractError: If an identity, configuration or lifecycle precondition is invalid.

        Example:
            research = app.open_research(method='human')
            research.create_experiment(profile_id, account_id, source=source_path,
                method='human', execution_mode='controlled')
            research.step()
            research.stop()
            research.close()
        """
        self._resources.check_open()
        if agent_factory is not None and (method != 'agent' or not callable(agent_factory)):
            raise ContractError('agent_factory requires method=agent and a trusted callable')
        from functools import partial

        controller: ResearchController
        common: Dict[str, Any] = dict(
            market_factory=lambda symbol, book=None, bar_origin=None: self._new_research_market(
                symbol,
                library=library,
                bar_origin=bar_origin,
                **(dict(depth=book['depth'], bar_spread=book['spread']) if book else {}),
            ),
            files=self._dataset_files,
            export_writer=self._export_writer,
        )
        if method == 'agent':
            from sim_platform.research.agent_controller import AgentResearchController

            controller = AgentResearchController(
                self._operations,
                **common,
                agent_factory=partial(self._new_agent_run, agent_factory=agent_factory),
                agent_experiment_factory=partial(
                    self._new_agent_experiment_run, agent_factory=agent_factory
                ),
            )
        elif method in ('human', 'rule', 'hold', 'model'):
            from sim_platform.research.policy_controller import PolicyResearchController

            controller = PolicyResearchController(
                self._operations,
                **common,
                runner_factory=self._new_runner,
                live_runner_factory=self._new_live_policy_runner,
            )
        else:
            raise ContractError('unknown research method')
        controller.on_close(lambda: self._resources.remove(controller))
        return self._resources.add(controller, shutdown_first=True)

    def open_experiment(
        self,
        profile: str,
        account: str,
        *,
        source: Union[PathInput, Sequence[PathInput]],
        method: ExperimentMethod = 'human',
        settings: Optional[JsonObject] = None,
        agent_id: Optional[str] = None,
        revision: Optional[int] = None,
        execution_config: Optional[ExecutionConfig] = None,
        log_path: Optional[PathInput] = None,
        library: Optional[PathInput] = None,
        clock: Optional[SharedExperimentClock] = None,
        execution_mode: ExecutionMode,
        agent_factory: Optional[AgentFactory] = None,
        book: Optional[Dict[str, int]] = None,
        allow_short: bool = False,
    ) -> ResearchController:
        """Prepare a trading experiment so the caller can start it with play().

        Use this when you already know the account, market file and decision method. The
        method prepares the historical data, saves a run record, and connects the decision
        runner and trading components. It returns a controller for playing, pausing, stopping
        and inspecting this experiment. The call waits for preparation; use open_research()
        if your UI needs to show preparation progress instead.

        The experiment is ready when this returns, but its clock has not started. For human
        or rule policies, Controlled lets the caller advance execution and Live runs it in
        the background. Agent execution currently runs in the background at 1x. A saved run
        can already say 'running' while its controller is ready or paused; that database
        field only tells you whether the run has ended.

        Platform closes the controller, and the controller closes its market and runner.
        If setup fails, partial resources are closed and any created run is cleaned up.
        Agent setup needs a saved agent revision and complete settings. A configured
        agent uses the built-in prompt/tool template. Call configure_experiment_model()
        before Play to enable it; without model setup it waits without consuming work.
        The template preserves context across rounds and explicitly finishes or waits.

        Args:
            agent_factory: Optional trusted, spawn-picklable create_agent(context) callable.
                Advanced Python composition hook. Normal callers omit it: configured Agents
                use the template; imported Agents load their saved source and ready environment.
            profile: Profile ID selecting whose saved data and account to use.
            account: Account ID to trade with; the account must belong to profile.
            source: Source file path for experiment preparation.
            method: Who makes decisions: human for manual input, rule for the built-in rule
                policy, hold for the hold policy, model for a user's model file (Controlled
                mode only; see docs/user-guide/model-policies.md), or agent for a saved
                agent definition.
            settings: Caller-owned experiment settings; policy methods allow defaults, Agent
                requires a complete settings object. Policy settings include start_mode,
                start_time_ns and, for a custom start on a price-bar recording that covers
                several days, start_date ('YYYY-MM-DD'; start_time_ns is then a time of day).
            agent_id: Registered Agent definition ID, not a maker or run ID.
            revision: Positive saved Agent revision number.
            execution_config: Rules for order validation and fills. None uses the default
                ExecutionConfig.
            log_path: Absolute path for a new JSON activity log. None chooses the default
                local path for this run.
            library: Path to the compiled market-data library. Usually omit this to use
                the configured or bundled library.
            clock: Optional trusted shared experiment clock for test/advanced composition;
                normal callers omit it.
            execution_mode: Required playback mode: controlled for caller-driven policy
                execution, live for background execution. Agent currently uses
                background 1x in either mode.
            book: For a price-bar (.csv) recording only: the synthetic book's spread
                (best ask minus best bid, integer money in whole cents; 200 is $0.02, the
                default) and depth (levels per side, 1..100; default 5), as
                {'spread': 300, 'depth': 10}. Recorded in the prepared manifest. See
                docs/reference/price-bars.md.
            allow_short: Let sales go beyond the shares held, under the margin rule in
                docs/reference/orders.md, which is saved with the run. Manual, rule, hold
                and model methods only; default False.

        Returns:
            ResearchController: The object used to control this experiment. Its market data
            is prepared, its run is saved, and its decision runner is connected.
            preparation.status is ready; call play() to start the clock.

        Raises:
            ContractError: If options, ownership, source coverage or binding are invalid.
            OSError: If source/prepared storage is unavailable; native/import/child failures propagate.

        Example:
            experiment = app.open_experiment(profile_id, account_id,
                source=source_path, method='human', execution_mode='live')
            # The experiment is ready here; the market clock has not started.
            experiment.play()
            experiment.pause()
            experiment.play()  # Resume.
            experiment.stop()
            experiment.close()
        """
        from sim_platform.domain.experiment import validate_execution_mode

        validate_execution_mode(execution_mode)
        experiment = self.open_research(method=method, library=library, agent_factory=agent_factory)
        try:
            if allow_short and method == 'agent':
                raise ContractError('short selling is not available for Agent experiments yet')
            if allow_short and method != 'agent':
                settings = dict(settings or {}, allow_short=True)
            if book is not None and method != 'agent':
                settings = dict(settings or {}, book=dict(book))
            elif book is not None:
                # Select the source with its book; the Agent setup keeps the selection.
                symbol = str((settings or {}).get('symbol', 'AAPL'))
                experiment.begin_prepare(cast('PathInput', source), symbol, book=book)
                experiment.wait_for_preparation()
            experiment.create_experiment(
                profile,
                account,
                # A list of sources (one per symbol) is read by the policy controller.
                source=cast('PathInput', source),
                method=method,
                settings=settings,
                agent_id=agent_id,
                revision=revision,
                execution_config=execution_config,
                log_path=log_path,
                clock=clock,
                execution_mode=execution_mode,
            )
            return experiment
        except Exception as exc:
            cleanup_after_error(exc, experiment.close)
            raise

    def open_agent_experiment(
        self,
        profile: str,
        account: str,
        agent_id: str,
        revision: int,
        *,
        source: PathInput,
        settings: JsonObject,
        execution_config: Optional[ExecutionConfig] = None,
        log_path: Optional[PathInput] = None,
        library: Optional[PathInput] = None,
        clock: Optional[SharedExperimentClock] = None,
        execution_mode: ExecutionMode,
        agent_factory: Optional[AgentFactory] = None,
    ) -> AgentResearchController:
        """Prepare an agent trading experiment from a saved agent revision.

        Use this convenience form when the decision method is always agent. agent_id and
        revision choose its saved definition; account, source and settings choose this test.
        It calls open_experiment(method='agent') and returns the same kind of ready controller.
        There is no separate experiment workflow behind this method.

        Preparation finishes before the call returns. Call play() to start. Platform closes
        the controller and its resources. See open_experiment() for supported playback modes
        and cleanup behavior.

        Args:
            agent_factory: Optional trusted, spawn-picklable create_agent(context) callable.
                Advanced Python composition hook. Normal callers omit it: configured Agents
                use the template; imported Agents load their saved source and ready environment.
            profile: Profile ID selecting whose saved data and account to use.
            account: Account ID to trade with; the account must belong to profile.
            agent_id: Registered Agent definition ID, not a maker or run ID.
            revision: Positive saved Agent revision number.
            source: Source file path for experiment preparation.
            settings: Caller-owned experiment settings; policy methods allow defaults, Agent
                requires a complete settings object.
            execution_config: Rules for order validation and fills. None uses the default
                ExecutionConfig.
            log_path: Absolute path for a new JSON activity log. None chooses the default
                local path for this run.
            library: Path to the compiled market-data library. Usually omit this to use
                the configured or bundled library.
            clock: Optional trusted shared experiment clock for test/advanced composition;
                normal callers omit it.
            execution_mode: Required playback mode: controlled for caller-driven policy
                execution, live for background execution. Agent currently uses
                background 1x in either mode.

        Returns:
            AgentResearchController: The same ready result as open_experiment(method="agent");
            no Play or model request yet.

        Raises:
            ContractError: If an identity, configuration or lifecycle precondition is invalid.

        Example:
            experiment = app.open_agent_experiment(profile_id, account_id, agent_id, revision,
                source=source_path, settings=agent_settings, execution_mode='live')
        """
        # method='agent' always opens an AgentResearchController.
        return cast(
            'AgentResearchController',
            self.open_experiment(
                profile,
                account,
                source=source,
                method='agent',
                settings=settings,
                agent_id=agent_id,
                revision=revision,
                execution_config=execution_config,
                log_path=log_path,
                library=library,
                clock=clock,
                execution_mode=execution_mode,
                agent_factory=agent_factory,
            ),
        )

    def evaluation(
        self,
        profile_id: str,
        entries: Dict[str, List[str]],
        *,
        baseline: Optional[str] = None,
        metric: str = 'total_return',
        seed: int = 2026,
        resamples: int = 10_000,
        training_cutoffs: Optional[Dict[str, str]] = None,
        token_prices: Optional[Dict[str, float]] = None,
        trials: Optional[Dict[str, int]] = None,
    ) -> JsonObject:
        """Compare saved runs of Agents and baselines with confidence intervals and tests.

        Group finished runs into named entries, for example several runs of one Agent
        revision and one run of a rule. Every run must have traded the same market with the
        same starting cash and execution settings; otherwise the call fails and names the
        difference. Reading saved runs does not start or change any experiment.

        Each entry gets every figure in docs/user-guide/evaluation.md with a 95% bootstrap
        interval of its mean. With a baseline, every other entry is tested against it on
        ``metric``: difference in means, its interval and a two-sided permutation p-value,
        Holm-adjusted across entries. The same runs and seed always give the same report.

        Args:
            profile_id: Profile that owns the runs.
            entries: Entry name to the ids of its finished runs, at most 20 entries.
            baseline: Name of the entry the others are tested against; None for figures only.
            metric: Figure tested against the baseline, such as 'total_return' or
                'sharpe_ratio'.
            seed: Seed of the resampling generator, saved in the report.
            resamples: Bootstrap and permutation resamples, 100 to 1,000,000.
            training_cutoffs: Entry name to the training-data cutoff date (YYYY-MM-DD)
                of the model its Agent runs used, to flag runs on dates it may have seen.
            token_prices: {'input': ..., 'output': ...} in USD per million tokens, to
                price Agent runs' model usage; None leaves model_cost empty.
            trials: Entry name to how many configurations (prompts, parameters, Agent
                revisions) were tried before keeping this one. It deflates that entry's
                Sharpe ratio for the search. An entry whose runs all come from one Agent
                defaults to that Agent's number of saved revisions; others to one trial.
                The report's ``trial_sources`` says where each count came from.

        Returns:
            dict: A detached 'agent-evaluation-v1' report.

        Raises:
            ContractError: If a run is missing, not finished or not comparable, or an
                argument is invalid.

        Example:
            report = app.evaluation(profile_id, {'Agent': agent_runs, 'Rule': [rule_run]},
                                    baseline='Rule')
        """
        self._resources.check_open()
        from sim_platform.research.evaluation import (
            agent_model_usage,
            evaluate_reports,
            is_agent_run,
            memorisation_flags,
        )
        from sim_platform.research.reports import run_report

        if not isinstance(entries, dict):
            raise ContractError('entries must map entry names to run ids')
        reports: Dict[str, List[Dict[str, Any]]] = {}
        for name, run_ids in entries.items():
            if not isinstance(run_ids, list) or not all(isinstance(r, str) for r in run_ids):
                raise ContractError(f'entry {name!r} must list run ids')
            reports[name] = []
            for run_id in run_ids:
                report = run_report(self, profile_id, run_id)
                if report['run']['status'] == 'running':
                    raise ContractError(f'run {run_id} has not finished')
                if is_agent_run(report):
                    report['model_usage'] = agent_model_usage(
                        self.agent_log_archive(profile_id, run_id)
                    )
                reports[name].append(report)
        cutoffs = training_cutoffs or {}
        if not isinstance(cutoffs, dict) or set(cutoffs) - set(entries):
            raise ContractError('training_cutoffs must map entry names to dates')
        flags = {
            report['run']['id']: memorisation_flags(report, cutoffs.get(name))
            for name, group in reports.items()
            for report in group
        }
        counted = self._revision_trials(profile_id, reports)
        given = dict(trials or {})
        result = evaluate_reports(
            reports,
            baseline=baseline,
            metric=metric,
            seed=seed,
            resamples=resamples,
            flags=flags,
            token_prices=token_prices,
            trials=dict(counted, **given),
        )
        result['trial_sources'] = {
            name: 'given' if name in given else 'agent revisions' if name in counted else 'one'
            for name in reports
        }
        return result

    def _revision_trials(
        self, profile_id: str, reports: Dict[str, List[Dict[str, Any]]]
    ) -> Dict[str, int]:
        """Trials counted from saved revisions for entries whose runs share one Agent.

        Every saved revision is a configuration someone tried, so the latest revision
        number is a floor on the search behind that Agent.
        """
        counted: Dict[str, int] = {}
        for name, group in reports.items():
            ids = {
                (((r['run'].get('config') or {}).get('agent') or {}).get('definition') or {}).get(
                    'agent_id'
                )
                for r in group
            }
            if len(ids) == 1 and None not in ids:
                counted[name] = int(self.agent(profile_id, str(ids.pop()))['revision'])
        return counted

    def split_recording(
        self,
        source: PathInput,
        output: PathInput,
        *,
        train_days: int,
        test_days: int,
        step_days: Optional[int] = None,
        gap_days: int = 0,
        anchored: bool = False,
    ) -> List[JsonObject]:
        """Cut a price-bar recording into walk-forward train and test files.

        Tune on each window's training file, then evaluate only on its test file, for
        example with ``open_batch().evaluate_suite(test_files, entries)``. No test date
        comes before the end of its training window. Reading and writing files does not
        change any experiment.

        Args:
            source: A price-bar CSV recording.
            output: Folder for the window files, created when missing.
            train_days: Trading dates per training window (the first one when anchored).
            test_days: Trading dates per test window.
            step_days: How far each window moves; defaults to test_days.
            gap_days: Dates skipped between training and test, for labels that look ahead.
            anchored: Keep every training window starting at the first date.

        Returns:
            list: Per window, ``train`` and ``test`` paths with ``train_dates`` and
                ``test_dates`` ([first, last]).

        Raises:
            ContractError: If the file is unreadable or too short for one window.

        Example:
            windows = app.split_recording(source_path, folder, train_days=250, test_days=60)
        """
        from sim_platform.research.walk_forward import split_recording

        return split_recording(
            source,
            output,
            train_days=train_days,
            test_days=test_days,
            step_days=step_days,
            gap_days=gap_days,
            anchored=anchored,
        )

    def disguise_recording(
        self,
        source: PathInput,
        output: Optional[PathInput] = None,
        *,
        start_date: str = '2100-01-04',
        price_scale: Optional[float] = None,
        seed: int = 2026,
        news: Optional[PathInput] = None,
        fundamentals: Optional[PathInput] = None,
        names: Optional[List[str]] = None,
        symbol: str = 'ASSET1',
        alias: str = 'the company',
    ) -> JsonObject:
        """Write a disguised copy of a price-bar CSV so a model cannot recall its market.

        Dates move by whole weeks into the future (weekdays kept) and every price is
        multiplied by one factor, rounded to the cent; volumes are kept and other columns
        are dropped. Use the copy as the recording for every method you compare, and give
        the experiment a neutral symbol such as 'ASSET1'. Evaluations flag Agent runs on
        future dates as 'disguised'. The original file is not changed.

        Args:
            source: Price-bar CSV file to disguise.
            output: Where to write the copy; default a neutral name under this Platform's
                directory, in 'disguised/'.
            start_date: Earliest first date of the copy (YYYY-MM-DD).
            price_scale: Factor applied to prices, 0.01 to 100; None draws one from [0.5, 2]
                with ``seed``.
            seed: Seed for the drawn factor.
            news: A news JSON file to disguise with the same date shift; headlines and
                periods have ``names`` replaced by ``alias`` and their dates moved.
            fundamentals: A fundamentals CSV to disguise the same way; its rows take
                ``symbol`` and its dates move with the prices.
            names: Company names and tickers to hide in text, such as ['Apple', 'AAPL'].
            symbol: The neutral symbol for disguised fundamentals rows.
            alias: The words that replace each listed name.

        Returns:
            dict: The mapping: 'source', 'output', 'first_date', 'disguised_first_date',
                'shift_days' and 'price_scale', plus 'news_output' and
                'fundamentals_output' when those files were given. Keep it away from the
                Agent.

        Raises:
            ContractError: If the file is not a valid price-bar CSV or an argument is invalid.

        Example:
            mapping = app.disguise_recording('aapl.csv')
            experiment = app.open_experiment(profile, account, source=mapping['output'],
                                             settings=dict(symbol='ASSET1'))
        """
        self._resources.check_open()
        import hashlib

        from sim_platform.market.prepared.bars import is_bar_source
        from sim_platform.market.prepared.disguise import disguise_price_bars

        path = Path(source)
        if not is_bar_source(path):
            raise ContractError('only price-bar CSV recordings can be disguised')
        if output is None:
            digest = hashlib.sha256(
                f'{path.resolve()}|{start_date}|{price_scale}|{seed}'.encode()
            ).hexdigest()[:12]
            output = self.directory / 'disguised' / f'disguised-{digest}.csv'
        mapping = dict(
            disguise_price_bars(
                path, Path(output), start_date=start_date, price_scale=price_scale, seed=seed
            )
        )
        from sim_platform.market.prepared.disguise_text import (
            disguise_fundamentals,
            disguise_news,
        )

        base = Path(mapping['output'])
        if news is not None:
            target = base.with_name(base.stem + '-news.json')
            disguise_news(Path(news), target, mapping['shift_days'], names or [], alias)
            mapping['news_output'] = str(target)
        if fundamentals is not None:
            target = base.with_name(base.stem + '-fundamentals.csv')
            disguise_fundamentals(
                Path(fundamentals), target, mapping['shift_days'], symbol, names or [], alias
            )
            mapping['fundamentals_output'] = str(target)
        return mapping

    def run_sweep(
        self,
        paths: List[PathInput],
        base: JsonObject,
        grid: Dict[str, List[Any]],
        *,
        metric: str = 'total_return',
        library: Optional[PathInput] = None,
        **arguments: Any,
    ) -> JsonObject:
        """Run a grid of parameters or prompts and return a results table.

        ``base`` is one suite entry, as for ResearchBatch.evaluate_suite(): a rule
        (``policy``, ``quantity``, ``hold_ns``), a model file (``model``) or a saved Agent
        (``agent_id``, ``revision``, ``settings``, ``model``). ``grid`` names entry fields
        by dotted path and lists values for each, for example ``{'quantity': [1, 5],
        'hold_ns': [60e9, 600e9]}`` or ``{'model.temperature': [0, 0.7]}``. For an Agent,
        the field ``prompt`` lists task prompts: each is saved as a new revision of the
        Agent (so evaluations count it as a trial) and run as its own combination.

        Every combination runs ``repetitions`` times on every recording, each run on a new
        account, so a sweep of Agents takes as long as all its windows. Each entry's
        deflated Sharpe ratio counts every combination as a trial, since picking the
        best cell is itself a search. Pass the table to research.sweeps.heatmap() and
        render_heatmap_svg() for a heat map.

        Args:
            paths: Recordings to run on (1 to 20).
            base: The suite entry every combination starts from.
            grid: Swept fields and their values (at most 200 combinations).
            metric: The figure tabulated and ranked (see evaluation metrics).
            library: Optional native library for the batch.
            **arguments: Further evaluate_suite() arguments (``profile``, ``cash``,
                ``fee_per_share``, ``seed``, ``model_provider``, ``concurrency``, ...).

        Returns:
            JsonObject: ``format`` 'market-lab-sweep-v1', ``metric``, ``grid``,
            ``combinations``, ``rows`` (``name``, ``parameters``, ``mean`` and per
            recording ``mean``, ``n``, ``interval``, ``sharpe``, ``deflated_sharpe``,
            ``run_ids``), ``best`` and the underlying ``suites``.

        Raises:
            ContractError: If the grid or an entry is invalid, or a prompt sweep is not
                for an Agent entry with a ``profile``.

        Example:
            result = app.run_sweep([recording], dict(name='rule', quantity=1),
                                   dict(quantity=[1, 5, 10], hold_ns=[60e9, 600e9]))
            from sim_platform.research.sweeps import heatmap, render_heatmap_svg
            svg = render_heatmap_svg(heatmap(result['rows'], 'quantity', 'hold_ns'))
        """
        from sim_platform.research.sweeps import run_sweep

        grid = dict(grid)
        prompts: Dict[int, str] = {}
        if 'prompt' in grid:
            prompts = self._prompt_revisions(base, grid.pop('prompt'), arguments.get('profile'))
            grid['revision'] = list(prompts)
        with self.open_batch(library=library) as batch:
            result = run_sweep(batch, paths, base, grid, metric=metric, **arguments)
        for row in result['rows']:
            if row['parameters'].get('revision') in prompts:
                row['parameters']['prompt'] = prompts[row['parameters']['revision']]
        if prompts:
            result['prompts'] = {str(k): v for k, v in prompts.items()}
        return result

    def _prompt_revisions(
        self, base: JsonObject, prompts: List[Any], profile_id: Optional[str]
    ) -> Dict[int, str]:
        """Save each prompt as a new revision of the base Agent; map revision to prompt."""
        if base.get('policy') != 'agent' or not profile_id:
            raise ContractError('a prompt sweep needs an Agent entry and the profile')
        if not isinstance(prompts, list) or not prompts:
            raise ContractError('prompt needs a list of task prompts')
        (agent_id, revision) = (base.get('agent_id'), base.get('revision'))
        if not isinstance(agent_id, str) or not isinstance(revision, int):
            raise ContractError('a prompt sweep needs the Agent entry\'s agent_id and revision')
        saved = self.agent(profile_id, agent_id, revision)
        latest = self.agent(profile_id, agent_id)['revision']
        revisions: Dict[int, str] = {}
        for prompt in prompts:
            if not isinstance(prompt, str) or not prompt.strip():
                raise ContractError('each prompt must be nonempty text')
            definition = dict(saved['definition'], initial_prompt=prompt, revision=str(latest + 1))
            latest = self.update_agent(profile_id, agent_id, latest, definition)['revision']
            revisions[latest] = prompt
        return revisions

    def open_batch(self, *, library: Optional[PathInput] = None) -> ResearchBatch:
        """Create a helper for evaluating policies in separate experiments.

        Use evaluate() for one policy evaluation or compare() to compare results. Each
        evaluation uses an independent account and the same market-preparation workflow as
        an ordinary research experiment. Creating this helper does not run an evaluation.

        library selects the native market library used to prepare data. Collect the results
        of your evaluations, then close the batch. Platform also closes it during shutdown.

        Args:
            library: Path to the compiled market-data library. Usually omit this to use
                the configured or bundled library.

        Returns:
            ResearchBatch: No evaluation started; owns no active evaluation controller yet.

        Raises:
            ContractError: If an identity, configuration or lifecycle precondition is invalid.

        Example:
            batch = app.open_batch()
            result = batch.evaluate(source_path, policy='rule')
            batch.close()
        """
        self._resources.check_open()
        from sim_platform.research.batch import ResearchBatch

        from functools import partial

        from sim_platform.research.batch import _evaluate_agent

        batch = ResearchBatch(
            # Batch comparisons use the default (policy) method.
            lambda: cast('PolicyResearchController', self.open_research(library=library)),
            self.directory,
            export_writer=self._export_writer,
            agent_evaluator=partial(_evaluate_agent, self),
        )
        batch.on_close(lambda: self._resources.remove(batch))
        return self._resources.add(batch)

    def open_datasets(
        self, *, demo_source: Optional[Tuple[PathInput, Optional[PathInput]]] = None
    ) -> DatasetLibrary:
        """Open the catalog used to find and import local market data files.

        Use the returned DatasetLibrary to manage which datasets appear in the app. Opening
        it may update an older folder-only catalog. If demo_source is supplied, that file is
        registered by its path; it is not copied. Later, add(copy=True) can explicitly copy
        an imported file.

        Listing or registering a file does not prepare its order book or create a trading
        experiment. Platform keeps the catalog open until you close it or close Platform.

        Args:
            demo_source: Optional (market_path, news_path) demonstration source pair; market is
                registered by reference.

        Returns:
            DatasetLibrary: Open catalog with saved preferences and entries loaded.

        Raises:
            ContractError: If an identity, configuration or lifecycle precondition is invalid.

        Example:
            datasets = app.open_datasets()
            catalog = datasets.catalog()
            datasets.close()
        """
        self._resources.check_open()
        from sim_platform.research.datasets import DatasetLibrary

        library = DatasetLibrary(self._dataset_files, demo_source)
        library.on_close(lambda: self._resources.remove(library))
        return self._resources.add(library)

    def replay_prepared_operations(
        self,
        profile: str,
        account: Optional[str],
        log_path: PathInput,
        *,
        cancelled: Optional[Callable[[], bool]] = None,
    ) -> PreparedReplayResult:
        """Check a recorded experiment by executing its original trading operations again.

        Use this to verify the saved outcome without asking the agent or model to decide
        again. Human, Rule and Agent logs use the same path. It reads a sealed operation
        log and prepared market data, then runs original trading commands at their original
        receipt times. Original Pause/Resume, model retries and waits are not re-executed.
        Actual cancellations caused by the end of the experiment are reproduced.

        The destination must belong to profile and match the original starting balances,
        holdings, fees and order history. The original run and its account are unchanged.
        The destination account is modified. This call waits for replay to finish and saves
        a new destination run. A failed replay can leave a stopped or failed destination run
        for inspection.

        Args:
            profile: Profile ID selecting whose saved data and account to use.
            account: Matching destination account ID, or None to create a new clean cash
                account. Prior holdings/history require an explicitly matching account.
            log_path: Absolute path to a sealed current-format original-operation JSON log.
            cancelled: Optional cooperative cancellation check, called between replay steps.

        Returns:
            PreparedReplayResult: A dictionary identifying the new replay run, with
            matched=True, the number of commands, financial comparison data,
            replay=True and model_calls=0. operations contains original trading requests
            in receipt order; events contains recomputed acceptance, rejection, fill and
            cancellation events with time_ns. Original fills are comparison evidence only.
            events_verified=True means every financial event matched the recorded list;
            older compatible logs without that list report False and compare final state.

        Raises:
            ContractError: If the log/artifact/initial account is unsupported or results differ.
            OSError: If the log or artifact cannot be read.

        Example:
            comparison = app.replay_prepared_operations(profile_id, replay_account_id, log_path)
            assert comparison['matched']
        """
        from sim_platform.persistence.agent_log_files import read_agent_log
        from .experiments.replay import replay

        self._resources.check_open()
        archive = read_agent_log(log_path)
        if account is None:
            from sim_platform.replay.plan import decode_plan

            initial = decode_plan(archive)[0]['initial_account']
            if (
                initial['positions']
                or initial['orders']
                or initial['fees']
                or initial['realized_pnl']
            ):
                raise ContractError(
                    'select a destination account matching the original portfolio/history'
                )
            if cancelled is not None and cancelled():
                raise ContractError('Replay cancelled')
            account = self.create_account(profile, 'Replay · original operations', initial['cash'])
        return cast(
            PreparedReplayResult,
            replay(
                self._operations,
                self._new_runner,
                profile,
                account,
                archive,
                cancelled=cancelled or (lambda: False),
            ),
        )

    def open_replay(self, *, library: Optional[PathInput] = None) -> ReplaySession:
        """Create a manual replay session for previously loaded raw-source inputs.

        This is an advanced building block. The caller supplies already loaded replay inputs
        and separate destination storage through the returned session. Platform tracks and
        closes that session.

        To verify or recover a saved file-source run, normally use replay_run() instead. It
        prepares the separate storage and handles cleanup for you.

        Args:
            library: Path to the compiled market-data library. Usually omit this to use
                the configured or bundled library.

        Returns:
            ReplaySession: A session ready for manual replay setup. No recorded operation
            has been replayed yet.

        Raises:
            ContractError: If an identity, configuration or lifecycle precondition is invalid.

        Example:
            session = app.open_replay()
            # session.run(...) requires validated replay inputs and isolated storage.
            session.close()
        """
        self._resources.check_open()
        from sim_platform.replay.runner import ReplaySession

        def prepared_market(
            symbol: str, depth: int, bar_spread: Optional[int], bar_origin: Optional[str] = None
        ) -> PreparedSession:
            return self._new_research_market(
                symbol, depth=depth, library=library, bar_spread=bar_spread, bar_origin=bar_origin
            )

        replay = ReplaySession(
            self._operations,
            market_factory=lambda symbol, depth: self._new_market(
                symbol, depth=depth, library=library
            ),
            runner_factory=self._new_runner,
            execution_factory=self._new_execution,
            prepared_market_factory=prepared_market,
        )
        replay.on_close(lambda: self._resources.remove(replay))
        return self._resources.add(replay)

    @classmethod
    def replay_run(
        cls,
        directory: Optional[PathInput],
        run_id: str,
        destination: PathInput,
        *,
        library: Optional[PathInput] = None,
        recover: bool = False,
        policy_bindings: Optional[Dict[str, Policy]] = None,
    ) -> Union[ReplayVerification, RecoveredRun]:
        """Verify a saved file-source run, or rebuild it so it can continue.

        This class method works without an existing Platform. It reads the original database
        and replays saved actions into a separate, empty destination directory. Verification
        compares the recalculated results; it does not ask the original policy or model to
        make new decisions. The original data is unchanged.

        With recover=False, it returns a verification dictionary and closes its temporary
        resources. With recover=True, it rebuilds a supported interrupted run to its saved
        checkpoint and returns a paused RecoveredRun that you must close. Recovery may need
        compatible policy_bindings keyed by maker ID. Live and agent operation logs use
        replay_prepared_operations() instead.

        Args:
            directory: Existing source platform data directory containing the saved run.
                None selects the per-OS-user default directory, as Platform() does.
            run_id: Existing run ID owned by the selected profile.
            destination: Empty isolated output directory; original source storage must be
                separate.
            library: Path to the compiled market-data library. Usually omit this to use
                the configured or bundled library.
            recover: False verifies a supported terminal run; True reconstructs a supported
                interrupted run.
            policy_bindings: Optional mapping of maker IDs to compatible Policy objects for
                recovery.

        Returns:
            ReplayVerification when recover=False, after isolated resources close. RecoveredRun
            when recover=True, paused and still open; the caller owns it.

        Raises:
            ContractError: If inputs/source identities, destination or recovery bindings are invalid.
            OSError: If database, source or output paths cannot be accessed.

        Example:
            verified = Platform.replay_run(source_directory, run_id, empty_destination)
            assert verified['verified']
            # For supported interrupted runs, use a different empty destination:
            with Platform.replay_run(source_directory, interrupted_id, recovery_directory,
                                     recover=True, policy_bindings=bindings) as recovered:
                recovered.owner.play()
        """
        from contextlib import ExitStack
        from dataclasses import asdict
        from sim_platform.domain.json_values import copy_json
        from sim_platform.application.use_cases.accounts import AccountService
        from .replay_manifest import validate_source
        from sim_platform.persistence.replay.storage import read_inputs, prepare_destination

        destination = Path(destination).resolve()
        # None selects the same per-OS-user default as Platform(directory=None).
        source = Path(directory).expanduser() if directory is not None else data_directory()
        inputs = read_inputs(
            source,
            run_id,
            recover=recover,
            snapshot_reader=lambda repo, profile, account: copy_json(
                asdict(AccountService(repo).snapshot(profile, account))
            ),
        )
        validate_source(inputs['source_info'], library)
        prepare_destination(source, run_id, destination, inputs)
        with ExitStack() as resources:
            app = resources.enter_context(cls(destination))
            session = app.open_replay(library=library)
            result = session.run(
                inputs, run_id, destination, recover=recover, policy_bindings=policy_bindings
            )
            if recover:
                # recover=True always produces a RecoveredRun.
                cast('RecoveredRun', result).adopt_lifetime(resources.pop_all(), app)
            return cast('Union[ReplayVerification, RecoveredRun]', result)

    def decision_runner(
        self, profile_id: str, run_id: str, maker_id: str, market: MarketInput, policy: Policy
    ) -> PolicyDecisionRunner:
        """Connect a saved policy run to the policy and market that will execute it.

        Use this after create_decision_run() when assembling an experiment manually. The
        policy must match the saved policy description, and the run and market must be at
        their required starting state. The returned runner calls the policy for decisions
        and sends its orders through the trading components.

        This runner executes synchronously when its methods are called. It owns its trading
        session, but borrows your market: close the runner before closing that market.
        Platform also tracks the runner. Use open_experiment() for automatic setup.

        Args:
            profile_id: Profile ID returned by create_profile(); selects whose data to access.
            run_id: Existing run ID owned by the selected profile.
            maker_id: Maker ID with current command authority for the run.
            market: Loaded market borrowed from the caller; it must match the run/source prefix.
            policy: Strategy object implementing decisions.contracts.Policy. For an
                existing run, its identity and settings must match the saved policy.

        Returns:
            PolicyDecisionRunner: Ready to request decisions from the supplied policy.
            It is connected to this run and market; playback has not started.

        Raises:
            ContractError: If an identity, configuration or lifecycle precondition is invalid.

        Example:
            runner = app.decision_runner(profile_id, run_id, maker_id, market, policy)
            runner.step()
            runner.close()
        """
        runner = self._new_runner(profile_id, run_id, maker_id, market, policy)
        runner.on_close(lambda: self._resources.remove(runner))
        return self._resources.add(runner)

    def abort_execution_run(self, profile_id: str, run_id: str) -> ExecutionSnapshot:
        """Stop a saved execution run that no longer has an active executor.

        Use this as a recovery action, for example after the process that owned the run has
        gone away. It obtains exclusive access to the account and performs the normal trading
        finish steps. Completed fills stay in the account and the run record is kept.

        If a session or runner still owns the run, stop it through that object instead.

        Args:
            profile_id: Profile ID returned by create_profile(); selects whose data to access.
            run_id: Existing run ID owned by the selected profile.

        Returns:
            ExecutionSnapshot: Final stopped financial state after cancellation of remaining
            orders.

        Raises:
            ContractError: If an identity, configuration or lifecycle precondition is invalid.

        Example:
            final = app.abort_execution_run(profile_id, orphaned_run_id)
            assert final.status == 'stopped'
        """
        return self._operations.abort_execution_run(profile_id, run_id)

    def decision_session(self, profile_id: str, run_id: str, maker_id: str) -> DecisionSession:
        """Get a small object for recording intents and strategy state on a plain run.

        Use the returned DecisionSession to observe the account, record a proposed action,
        and save or load your own policy state. It remembers the profile, run and maker IDs
        so you do not need to pass them on each call. The maker's right to control the run
        is checked when the session is created.

        A plain run has no trading engine attached: recording an intent does not check funds
        or match an order. Execution and policy-configured runs must use their execution
        session or runner instead. Keep Platform open while using this object; it has no
        separate close method.

        Args:
            profile_id: Profile ID returned by create_profile(); selects whose data to access.
            run_id: Existing run ID owned by the selected profile.
            maker_id: Maker ID with current command authority for the run.

        Returns:
            DecisionSession: An object remembering this run and maker for later calls.
            It shares Platform storage and creates no executor, thread or process.

        Raises:
            ContractError: If an identity, configuration or lifecycle precondition is invalid.

        Example:
            session = app.decision_session(profile_id, plain_run_id, maker_id)
            current = session.observe()
        """
        self._operations.validate_authority(profile_id, run_id, maker_id)
        return DecisionSession(self._operations, profile_id, run_id, maker_id)

    def close(self) -> None:
        """Stop this platform's work and release the resources it created.

        Call this when the app is done, or let a with block call it automatically. Experiments,
        processes and workers are closed before storage. This can wait while they finish
        outstanding work or shut down. Markets borrowed through manual setup APIs remain
        the caller's responsibility.

        Calling close() again after success is safe. If cleanup fails, the error is raised
        and unresolved resources remain tracked so you can retry. closed becomes True when
        shutdown starts, even if cleanup later fails.

        Returns:
            None. The platform stops accepting new resources as soon as shutdown begins.

        Raises:
            Exception: Child shutdown, financial finalization, storage or credential backend failure; retry after resolving it.

        Example:
            app.close()
            assert app.closed
        """
        if self.closed and not self._resources.has_roots:
            return
        storage = tuple(
            [self._database_instance.close] if self._database_instance is not None else []
        )
        storage += (self._workspace.close,)
        if self._credentials is not None:
            storage += (self._credentials.close,)
        try:
            self._resources.close()
        except Exception as exc:
            if not self._resources.has_live_execution:
                cleanup_after_error(exc, *storage)
            raise
        cleanup(*storage)

    def __enter__(self) -> Platform:
        """Use this Platform inside a with block.

        The block receives the same Platform object. Leaving the block calls close(), so
        experiments and workers are cleaned up even if the block raises an exception.
        Entering is rejected if shutdown has already started.

        Returns:
            Platform: This same instance.

        Raises:
            ContractError: If an identity, configuration or lifecycle precondition is invalid.

        Example:
            with Platform(storage_path) as app:
                profiles = app.profiles()
        """
        self._resources.check_open()
        return self

    def __exit__(self, *args: Any) -> None:
        """Close the platform when its with block ends.

        This calls close() whether the block finished normally or raised an exception. It
        does not suppress the block's exception. A cleanup failure can also raise an error.

        Args:
            args: Exception type, exception value and traceback supplied by the with statement;
                ignored, so exceptions are not suppressed.

        Returns:
            None. Exceptions from the with block are not suppressed.

        Raises:
            Exception: If resource or storage cleanup fails.
        """
        self.close()


class DecisionSession:
    """Record proposed actions and saved strategy state for one plain run.

    Obtain this object from Platform.decision_session(). It remembers which profile,
    run and maker your calls refer to. It can read the account, record an intent and
    save your own JSON state. An intent here is only a recorded proposal: this object
    does not check available funds or execute orders against a market.

    For actual trading, use an execution session or decision runner. This object shares
    its Platform's storage, so keep Platform open while using it.
    """

    def __init__(
        self, operations: ApplicationOperations, profile_id: str, run_id: str, maker_id: str
    ) -> None:
        """Remember which plain run and decision-maker this object will access.

        Platform.decision_session() supplies the shared operations and checks the IDs for
        the caller. This constructor stores those references; it does not create a market,
        thread or trading executor. The supplied operations remain owned by Platform.

        Args:
            operations: Coordinator owned by Platform; no ownership is transferred.
            profile_id: Owning profile ID, checked by the Platform factory.
            run_id: Plain run whose intents/state this facade accesses.
            maker_id: Maker with current command authority.

        Returns:
            None. No thread, execution owner or market is created.
        """
        self.__operations = operations
        self.__profile = profile_id
        self.__run = run_id
        self.__maker = maker_id

    def observe(self) -> AccountSnapshot:
        """Read the current account state for this session's run.

        Use this before recording a proposed action if you need to inspect cash, holdings
        or orders. It returns a snapshot for this call, not a live view. It does not advance
        the market or make a decision.

        Returns:
            AccountSnapshot: Detached current cash/positions/orders; no market cutoff
            is captured and no decision or order is executed.

        Raises:
            ContractError: If ownership/run identity is no longer valid.

        Example:
            account = session.observe()
            available_cash_before_reservations = account.cash
        """
        run = self.__operations.run(self.__profile, self.__run)
        return self.__operations.snapshot(self.__profile, run["account_id"])

    def submit(self, intent: Intent, time_ns: int) -> str:
        """Save a proposed buy or sell action as an intent on this plain run.

        The intent records what the decision-maker wanted to do and at what simulation
        time. This method does not check funds or match a trade against the market. Its
        returned string identifies the saved intent, not a filled order.

        Submitting the same client ID with identical content reuses the saved event.
        Different content under that ID is rejected. Use a trading runner for actual orders.

        Args:
            intent: Intent with shares and integer USD ten-thousandths limit price.
            time_ns: Nonnegative simulation nanoseconds, not a wall-clock timestamp.

        Returns:
            str: Persisted intent ID. This is not an acceptance/fill acknowledgement.
            Identical client IDs/content reuse the ledger event; conflicts fail.

        Raises:
            ContractError: If the run has execution settings, maker authority changed,
                the intent is invalid or its timestamp moves backwards.

        Example:
            intent_id = session.submit(Intent('request-1', 'AAPL', 'buy', 1, 1_000_000), 0)
        """
        return self.__operations._submit(self.__profile, self.__run, self.__maker, intent, time_ns)

    def save_state(self, value: JsonValue) -> None:
        """Save a JSON value that your strategy can read on its next call.

        For example, a simple strategy can remember how many observations it has processed.
        The value is stored for this plain run. Saving it does not place an order or produce
        a market event. It must be JSON-compatible data, not an API key or a database object.

        Args:
            value: JSON-compatible state; not an ORM object or credential container.

        Returns:
            None. The state is committed without producing trades or market events.

        Raises:
            ContractError: If maker authority is stale, JSON is invalid, or a
                decision-configured run owns its journal through a Runner.

        Example:
            session.save_state({'observations': 3})
        """
        self.__operations._policy_state(self.__profile, self.__run, self.__maker, value)

    def load_state(self) -> JsonValue:
        """Read the strategy value previously saved for this plain run.

        Use this to continue your own strategy bookkeeping. Before anything has been saved,
        the result is an empty dictionary. The result is data for this call; changing it does
        not save changes unless you call save_state(). It is not an agent conversation history
        or an automatic memory system.

        Returns:
            JsonValue: Detached stored policy_state, initially an empty dictionary.
            Does not include model context or create a memory system.

        Raises:
            ContractError: If the run/profile identity is invalid.

        Example:
            state = session.load_state()
        """
        return self.__operations.run(self.__profile, self.__run)["policy_state"]
