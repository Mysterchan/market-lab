"""Profiles, accounts, makers, runs and account records for Platform."""

from __future__ import annotations
from typing import (
    TYPE_CHECKING,
    ContextManager,
    Dict,
    List,
    Optional,
    Tuple,
    Union,
)
from .api_types import (
    AccountDetails,
    JsonObject,
    JsonValue,
    MakerDetails,
    MakerKind,
    ProfileDetails,
    ReportData,
    ReportStreams,
    ReportViewData,
    RunDetails,
    TerminalStatus,
)
from sim_platform.domain.models import AccountSnapshot, Intent

if TYPE_CHECKING:
    from os import PathLike
    from sim_platform.decisions.policy.contracts import Policy
    from sim_platform.decisions.policy.models import DecisionConfig
    from sim_platform.execution.models import ExecutionConfig
    from sim_platform.market.playback import MarketPlayback
    from sim_platform.market.prepared.session import PreparedSession

    MarketInput = Union[MarketPlayback, PreparedSession]

from .platform_base import PlatformBase


class PlatformRecords(PlatformBase):
    """Profiles, accounts, makers, runs and account records for Platform."""

    def create_profile(self, name: str) -> str:
        """Create a named group for a user's accounts, strategies and saved agents.

        Call this before creating that user's first account. It saves the profile and returns
        its ID. Use that ID in later calls to select the user's data. The name is a display
        label; two profiles can have the same name. No account or experiment is created.

        Args:
            name: Nonempty display name. Use the returned ID, rather than this name,
                to select the saved record later.

        Returns:
            str: The new profile ID. Pass it unchanged to account and agent methods.
            Creating a profile does not create an account.

        Raises:
            ContractError: If an identity, configuration or lifecycle precondition is invalid.

        Example:
            profile_id = app.create_profile('Research')
        """
        return self._operations.create_profile(name)

    def profiles(self) -> List[ProfileDetails]:
        """List the user profiles saved on this platform.

        Use the returned IDs and names to populate a profile picker. This reads saved data
        only; it does not include balances, experiments or current playback state.

        Returns:
            list[ProfileDetails]: One id/name dictionary per saved profile. The list is empty
            if none exist and does not update itself when profiles are added.

        Example:
            names = [item['name'] for item in app.profiles()]
        """
        return self._operations.profiles()

    def create_account(self, profile_id: str, name: str, cash: int, currency: str = 'USD') -> str:
        """Create a cash account with the starting balance for your experiments.

        The account belongs to the selected profile. Its initial cash and opening history
        record are saved together. The returned ID lets you choose this account when creating
        an experiment. No trading starts here.

        cash is an integer in ten-thousandths of a US dollar: pass 1_000_000 for USD 100.
        Only USD is currently supported.

        Args:
            profile_id: Profile ID returned by create_profile(); selects whose data to access.
            name: Nonempty display name. Use the returned ID, rather than this name,
                to select the saved record later.
            cash: Nonnegative integer initial cash in USD ten-thousandths (10,000 = USD 1).
            currency: Account currency; currently only USD is accepted.

        Returns:
            str: The new account ID, belonging to profile_id. Use it to select this account
            in later experiment and snapshot calls.

        Raises:
            ContractError: If an identity, configuration or lifecycle precondition is invalid.

        Example:
            account_id = app.create_account(profile_id, 'Cash', 1_000_000)  # USD 100.
        """
        return self._operations.create_account(profile_id, name, cash, currency)

    def accounts(self, profile_id: str) -> List[AccountDetails]:
        """List the saved accounts belonging to one profile.

        Use this to choose an account for an experiment. Each item describes the account's
        ID, name, owner and currency. To read its current cash, holdings and orders, call
        snapshot() with its ID.

        Args:
            profile_id: Profile ID returned by create_profile(); selects whose data to access.

        Returns:
            list[AccountDetails]: One id/profile_id/name/currency dictionary per account.
            Balances and orders are not included.

        Raises:
            ContractError: If an identity, configuration or lifecycle precondition is invalid.

        Example:
            accounts = app.accounts(profile_id)
        """
        return self._operations.accounts(profile_id)

    def create_maker(
        self,
        profile_id: str,
        name: str,
        kind: MakerKind,
        version: str,
        config: Optional[JsonObject] = None,
    ) -> str:
        """Register the identity of whoever will make a run's decisions.

        A maker can represent a human, a rule, a model or an agent. The saved name, kind,
        revision label and config identify that decision-maker in later run records. Creating
        a maker does not execute a strategy, look up an API key or create an experiment.

        Use this for manual run setup. Normal open_experiment() setup connects the appropriate
        decision-maker for you. config is saved JSON metadata; version is your chosen label.

        Args:
            profile_id: Profile ID returned by create_profile(); selects whose data to access.
            name: Nonempty display name. Use the returned ID, rather than this name,
                to select the saved record later.
            kind: Decision-maker kind: human, rule, model or agent.
            version: Caller-defined maker revision label; not an internal implementation
                version.
            config: Optional finite-JSON maker metadata; None becomes an empty object.

        Returns:
            str: The new maker ID. Pass it to manual run creation methods.

        Raises:
            ContractError: If an identity, configuration or lifecycle precondition is invalid.

        Example:
            maker_id = app.create_maker(profile_id, 'Manual', 'human', '1')
        """
        return self._operations.create_maker(profile_id, name, kind, version, config)

    def makers(self, profile_id: str) -> List[MakerDetails]:
        """List the decision-maker identities saved under a profile.

        Use this to find a maker ID for manual run setup or to show its saved name and
        configuration. Reading a model or agent entry does not start it.

        Args:
            profile_id: Profile ID returned by create_profile(); selects whose data to access.

        Returns:
            list[MakerDetails]: Dictionaries describing the makers and their saved settings.

        Raises:
            ContractError: If an identity, configuration or lifecycle precondition is invalid.

        Example:
            makers = app.makers(profile_id)
        """
        return self._operations.makers(profile_id)

    def create_run(
        self,
        profile_id: str,
        account_id: str,
        maker_id: str,
        universe: List[str],
        config: Optional[JsonObject] = None,
    ) -> str:
        """Create a basic run record for manually recording a decision-maker's activity.

        A run groups one account, one maker and the symbols being considered. This method
        saves their starting account state, but does not connect a market, trading engine
        or worker. The account must belong to the profile and have no active run or open
        orders; symbol names must be nonempty and distinct.

        Use create_execution_run() for manual order execution, create_decision_run() for a
        policy runner, or open_experiment() for complete experiment setup.

        Args:
            profile_id: Profile ID returned by create_profile(); selects whose data to access.
            account_id: Account ID returned by create_account(); it must belong to this profile.
            maker_id: Maker ID with current command authority for the run.
            universe: Nonempty list of distinct uppercase symbols; financial execution requires
                one symbol.
            config: Optional finite-JSON run metadata; None becomes an empty object.

        Returns:
            str: New run ID with database status="running" and time_ns=0.

        Raises:
            ContractError: If an identity, configuration or lifecycle precondition is invalid.

        Example:
            run_id = app.create_run(profile_id, account_id, maker_id, ['AAPL'])
        """
        return self._operations.create_run(profile_id, account_id, maker_id, universe, config)

    def _record_lineage(self, event: str, profile_id: str, run_id: str) -> None:
        """Record lineage when a run binds to its market ('bind') or ends ('finish')."""
        if event == 'bind':
            self._lineage.record_start(profile_id, run_id)
        elif event == 'finish':
            self._lineage.record_finish(profile_id, run_id)

    def run_lineage(self, profile_id: str, run_id: str) -> JsonObject:
        """Read what a run used: its code, configuration, input digests and record seals.

        Every run records its lineage automatically, in three write-once parts that are
        never replaced: ``inputs`` when it binds to its market (package version, Git
        commit and whether the checkout had uncommitted changes, execution-rule and
        native-library digests, a configuration digest and the SHA-256 of each input
        file or folder: recording, news, fundamentals, documents, model file),
        ``records`` when it ends (status and digests of its events and decisions) and,
        for an Agent, ``agent`` when its log is archived (log digest, model profile,
        served model versions, Agent definition digest). A part not yet recorded is
        None. Use verify_run() to check that nothing has changed since.

        Args:
            profile_id: Profile ID returned by create_profile(); selects whose data to access.
            run_id: Existing run ID owned by the selected profile.

        Returns:
            JsonObject: ``run_id`` plus the parts ``inputs``, ``records`` and ``agent``.

        Raises:
            ContractError: If the profile or run does not exist or the profile does not own it.

        Example:
            lineage = app.run_lineage(profile_id, run_id)
            commit = lineage['inputs']['code'].get('commit')
            data = [(i['field'], i['sha256']) for i in lineage['inputs']['inputs']]
        """
        return self._lineage.lineage(profile_id, run_id)

    def verify_run(self, profile_id: str, run_id: str) -> JsonObject:
        """Prove a stored run is unchanged, or show exactly what changed.

        Recomputes the digest of the run's configuration, of every input file or folder
        it recorded, of its events and decisions and of its archived Agent log, and
        compares each with its lineage. Hashing reads every input file, so a large
        recording takes a few seconds the first time in a process.

        Args:
            profile_id: Profile ID returned by create_profile(); selects whose data to access.
            run_id: Existing run ID owned by the selected profile.

        Returns:
            JsonObject: ``unchanged`` (True when every check matches), ``sealed`` (the run
            has ended and its records are sealed), ``checks`` (each ``item``, ``recorded``,
            ``current`` and ``match``) and ``code_changed`` (the running code's version,
            commit or execution rules differ from the run's; Replay may then disagree).

        Raises:
            ContractError: If the profile or run does not exist or the profile does not own it.

        Example:
            proof = app.verify_run(profile_id, run_id)
            assert proof['unchanged'], [c for c in proof['checks'] if not c['match']]
        """
        return self._lineage.verify(profile_id, run_id)

    def pnl_attribution(
        self,
        profile_id: str,
        run_id: str,
        *,
        factors: Optional[Dict[str, List[float]]] = None,
    ) -> JsonObject:
        """Split a finished run's result into alpha, market beta, factor exposure and costs.

        Regresses the run's per-bar equity returns on the equal-weight buy-and-hold
        return of its symbols (ordinary least squares with t-statistics), and on any
        extra ``factors`` you supply, and breaks the money paid on fills into commission,
        borrow and market impact. Alpha is per bar and annualised from the bar length.

        Args:
            profile_id: Profile ID returned by create_profile(); selects whose data to access.
            run_id: A finished run with an archived market (bars) owned by the profile.
            factors: Optional factor return series by name (for example Fama-French
                daily returns as decimals), one value per period of the run's equity
                curve, in order.

        Returns:
            JsonObject: ``periods``, ``periods_per_year``, ``market`` (``alpha``,
            ``alpha_t``, ``alpha_annual``, ``betas``, ``r_squared``,
            ``residual_volatility``), ``factors`` (the same with every factor, when
            given), ``costs`` (``commission``, ``borrow``, ``impact``, ``total`` in
            money units and each in bps of traded notional) and ``pnl`` (``net`` and
            ``gross`` before costs). See research.pnl_attribution.

        Raises:
            ContractError: If the profile or run does not exist or the profile does not own it.

        Example:
            figures = app.pnl_attribution(profile_id, run_id)
            print(figures['market']['alpha_annual'], figures['market']['betas']['market'])
        """
        from sim_platform.research.pnl_attribution import pnl_attribution
        from sim_platform.research.reports import report_view

        return pnl_attribution(report_view(self, profile_id, run_id), factors)

    def model_risk_report(
        self,
        profile_id: str,
        run_ids: List[str],
        *,
        path: Optional[Union[str, 'PathLike[str]']] = None,
        title: str = 'Model risk report',
        baseline: Optional[str] = None,
    ) -> JsonObject:
        """Collect the assumptions, data, tests and limits of finished runs in one report.

        For each run: the execution assumptions in plain words (fill model, latency,
        commission, impact, borrow), the data and code from its lineage (each input's
        SHA-256, version and commit), the Agent and model (provider, temperature, served
        versions, calls, tokens, budget), its result figures, memorisation flags and the
        result of verify_run(). With two or more runs it also compares them as entries,
        with probabilistic and deflated Sharpe ratios and PBO. Known limits close it.
        Verifying hashes every input file, so large recordings take a few seconds.

        Args:
            profile_id: Profile ID returned by create_profile(); selects whose data to access.
            run_ids: One or more finished runs owned by the profile.
            path: Optional file to write: ``.md`` writes Markdown, anything else JSON.
            title: The report's title.
            baseline: Optional run ID the other runs are tested against.

        Returns:
            JsonObject: ``format``, ``title``, ``created_at``, ``runs`` (one section per
            run), ``limits`` and, for several runs, ``evaluation``.

        Raises:
            ContractError: If no run is given, or a run does not exist or is not the profile's.
            OSError: If ``path`` cannot be written.

        Example:
            report = app.model_risk_report(profile_id, [agent_run, rule_run],
                                           path='/absolute/path/review.md',
                                           baseline=rule_run)
        """
        import json
        from pathlib import Path

        from sim_platform.domain.models import ContractError
        from sim_platform.persistence.export_files import atomic_write
        from sim_platform.research.model_risk import model_risk_report, render_markdown

        if not run_ids:
            raise ContractError('choose at least one run for a model risk report')
        report = model_risk_report(self, profile_id, run_ids, title=title, baseline=baseline)
        if path is not None:
            target = Path(path)
            text = (
                render_markdown(report)
                if target.suffix.lower() == '.md'
                else json.dumps(report, indent=2, sort_keys=True) + '\n'
            )
            atomic_write(target, lambda stream: stream.write(text))
        return report

    def diff_runs(self, profile_id: str, run_a: str, run_b: str) -> JsonObject:
        """List what differs between two runs: settings, prompt, model, data, code, results.

        Use it to explain why two results differ. Each group lists the differing fields
        as ``{'field', 'a', 'b'}``: ``execution`` (fill model, latency, costs),
        ``settings`` (the rest of the run configuration, including Agent settings and
        the policy's parameters), ``prompt`` (both task prompts when they differ),
        ``model`` (provider, model, temperature and the versions served), ``data``
        (input file digests by field) and ``code`` (version, commit, rule digests).
        ``results`` sets the main figures side by side with the difference b minus a.
        Identities and caches (run IDs, file times, prepared-data paths) are ignored.

        Args:
            profile_id: Profile ID returned by create_profile(); selects whose data to access.
            run_a: The first finished run.
            run_b: The second finished run.

        Returns:
            JsonObject: ``a``, ``b``, ``execution``, ``settings``, ``prompt``, ``model``,
            ``data``, ``code`` and ``results``. Empty lists mean no difference.

        Raises:
            ContractError: If a run does not exist or the profile does not own it.

        Example:
            diff = app.diff_runs(profile_id, first_run, second_run)
            changed = [d['field'] for d in diff['execution'] + diff['settings']]
        """
        from sim_platform.research.run_diff import diff_runs

        return diff_runs(self, profile_id, run_a, run_b)

    def runs(self, profile_id: str, *, include_records: bool = True) -> List[RunDetails]:
        """List the saved run records belonging to a profile.

        Use this for an experiment history screen. include_records=False avoids loading
        full decision histories when only summary data is needed. Returned statuses describe
        whether each saved run has ended, not whether its playback is currently paused.
        Reading this list does not move any experiment forward.

        Args:
            profile_id: Profile ID returned by create_profile(); selects whose data to access.
            include_records: Include the full policy decision record journal when True.

        Returns:
            list[RunDetails]: A dictionary for each saved run. Use a controller, rather than
            these dictionaries, to play, pause or stop execution.

        Raises:
            ContractError: If an identity, configuration or lifecycle precondition is invalid.

        Example:
            runs = app.runs(profile_id, include_records=False)
        """
        return self._operations.runs(profile_id, include_records=include_records)

    def decision_records(
        self, profile_id: str, run_id: str, *, limit: Optional[int] = None
    ) -> List[JsonObject]:
        """Read the saved decisions that a policy made during a run.

        Use this to inspect what the strategy decided and the details recorded for it. It
        reads history without calling the policy again. Omit limit to load all records, or
        supply a positive limit to keep only the latest records. Full histories may be large.

        Args:
            profile_id: Profile ID returned by create_profile(); selects whose data to access.
            run_id: Existing run ID owned by the selected profile.
            limit: Positive tail/page bound, or None for all records where supported.

        Returns:
            list[JsonObject]: Detached decision records in stored order.

        Raises:
            ContractError: If an identity, configuration or lifecycle precondition is invalid.

        Example:
            tail = app.decision_records(profile_id, run_id, limit=20)
        """
        return self._operations.decision_records(profile_id, run_id, limit=limit)

    def run_events(
        self, profile_id: str, run_id: str, *, limit: Optional[int] = None
    ) -> List[JsonObject]:
        """Read the saved financial and audit events associated with a run.

        Use this to inspect the run's recorded account changes and execution events. Omit
        limit for the full list, or set a positive limit for the latest events, returned in
        sequence order. Tool calls still in the local JSON file are not included here.

        Args:
            profile_id: Profile ID returned by create_profile(); selects whose data to access.
            run_id: Existing run ID owned by the selected profile.
            limit: Positive tail/page bound, or None for all records where supported.

        Returns:
            list[JsonObject]: Detached account_id/sequence/event_id/run_id/time_ns/kind/data
            records.

        Raises:
            ContractError: If an identity, configuration or lifecycle precondition is invalid.

        Example:
            tail = app.run_events(profile_id, run_id, limit=50)
        """
        return self._operations.run_events(profile_id, run_id, limit=limit)

    def archive_market(self, profile_id: str, run_id: str, archive: JsonObject) -> None:
        """Save the final market data needed to display a finished run's report.

        Controllers use this after execution has ended. The supplied archive is a JSON data
        summary for report readers; it is not a market source that can be played again or a
        saved order book for resuming execution.

        The run must use policy decisions or a connected agent, and an agent's execution
        owner must have stopped. Normal controller cleanup handles this for its caller.

        Args:
            profile_id: Profile ID returned by create_profile(); selects whose data to access.
            run_id: Existing run ID owned by the selected profile.
            archive: Finite-JSON report projection to associate with the run.

        Returns:
            None. Existing run archive is updated; no market event is executed.

        Raises:
            ContractError: If an identity, configuration or lifecycle precondition is invalid.

        Example:
            app.archive_market(profile_id, run_id, terminal_market_projection)
        """
        return self._operations.archive_market(profile_id, run_id, archive)

    def report_stream(
        self, profile_id: str, run_id: str
    ) -> ContextManager[Tuple[ReportViewData, ReportStreams]]:
        """Read a report without loading all of its history into memory at once.

        Use this for large exports. Enter the returned context manager with a with block.
        It gives you the report summary and iterators for account events, run events and
        decisions, all read from one consistent database view.

        Consume the iterators inside that block. The read transaction stays open until the
        block exits, and the iterators cannot be used afterward. account_ledger and run_events
        yield event dictionaries; decisions and nested_decisions yield decision dictionaries.

        Args:
            profile_id: Profile ID returned by create_profile(); selects whose data to access.
            run_id: Existing run ID owned by the selected profile.

        Returns:
            ContextManager[tuple[ReportViewData, ReportStreams]]: The with block yields metadata
            and four single-pass iterators.

        Raises:
            ContractError: If an identity, configuration or lifecycle precondition is invalid.

        Example:
            with app.report_stream(profile_id, run_id) as (view, streams):
                for event in streams['run_events']:
                    write_event(event)
        """
        return self._operations.report_stream(profile_id, run_id)

    def report_view_data(self, profile_id: str, run_id: str) -> ReportViewData:
        """Read the account and market summary used to display a run's report.

        Use this for an on-screen report when you do not need every recorded event. A saved
        final summary is used when available. For an active run it reads the current account;
        for older finished runs it may rebuild the account at that run's end from history.

        This is display data. It does not capture market and account data at one synchronized
        trading instant, so it should not replace an agent's trading-tool query.

        Args:
            profile_id: Profile ID returned by create_profile(); selects whose data to access.
            run_id: Existing run ID owned by the selected profile.

        Returns:
            ReportViewData: Run/final/last_market, up to 100 run events and decisions, counts,
            and an empty account_ledger list.

        Raises:
            ContractError: If an identity, configuration or lifecycle precondition is invalid.

        Example:
            view = app.report_view_data(profile_id, run_id)
            filled = view['fill_count']
        """
        return self._operations.report_view_data(profile_id, run_id)

    def report_data(self, profile_id: str, run_id: str) -> ReportData:
        """Read a run's full report, including its history and ending account state.

        Use this when you need all evidence in one Python value. The account is rebuilt only
        through this run's last event, so later trades in the same account do not change the
        historical result. It does not rerun the strategy.

        The whole result is loaded into memory. Use report_stream() for large exports.

        Args:
            profile_id: Profile ID returned by create_profile(); selects whose data to access.
            run_id: Existing run ID owned by the selected profile.

        Returns:
            ReportData: run, final, last_market, account_ledger and run_events.

        Raises:
            ContractError: If an identity, configuration or lifecycle precondition is invalid.

        Example:
            report = app.report_data(profile_id, run_id)
            historical_cash = report['final']['cash']
        """
        return self._operations.report_data(profile_id, run_id)

    def create_managed_decision_run(
        self,
        profile_id: str,
        account_id: str,
        symbol: str,
        policy: Policy,
        config: DecisionConfig,
        execution_config: ExecutionConfig,
        *,
        source: Optional[JsonObject] = None,
        allow_short: bool = False,
    ) -> Tuple[str, str]:
        """Save a policy's maker identity and run record together.

        The research workflow uses this during setup. It records which policy is responsible,
        how decisions are scheduled, the trading rules and optional market source information.
        Both records are written in one transaction so setup cannot save only half of them.

        The result is (maker_id, run_id). No runner or playback starts here; those are connected
        later. policy must implement decisions.contracts.Policy. Most callers can let
        open_experiment() perform this setup.

        Args:
            profile_id: Profile ID returned by create_profile(); selects whose data to access.
            account_id: Account ID returned by create_account(); it must belong to this profile.
            symbol: Stock symbol, such as AAPL. Trading experiments currently use one
                symbol per run; standalone playback selects the book for this symbol.
            policy: Strategy object implementing decisions.contracts.Policy. For an
                existing run, its identity and settings must match the saved policy.
            config: Required DecisionConfig controlling policy invocation and observation.
            execution_config: Required ExecutionConfig controlling financial rules.
            source: Optional finite-JSON source description saved with the run.
            allow_short: Allow sales beyond the shares held under the margin rule in
                docs/reference/orders.md; the rule is saved with the run.

        Returns:
            tuple[str, str]: (maker_id, run_id), in that order; run database status is running.

        Raises:
            ContractError: If an identity, configuration or lifecycle precondition is invalid.

        Example:
            maker_id, run_id = app.create_managed_decision_run(profile_id, account_id, 'AAPL',
                policy, decision_config, execution_config, source=source_metadata)
        """
        return self._operations.create_managed_decision_run(
            profile_id,
            account_id,
            symbol,
            policy,
            config,
            execution_config,
            source=source,
            allow_short=allow_short,
        )

    def finish_run(
        self, profile_id: str, run_id: str, status: TerminalStatus = 'completed'
    ) -> None:
        """Mark a run as ended after its actual work has already been finished.

        This saves completed, stopped or failed along with the final account summary. It
        does not cancel orders, stop processes or close the experiment controller. To stop
        an active experiment, call the controller's stop() instead.

        A plain run can be completed only after orders and intents are resolved. A run with
        financial execution must already have a matching execution_end event; finish it
        through its execution session or runner so the required cleanup happens first.

        Args:
            profile_id: Profile ID returned by create_profile(); selects whose data to access.
            run_id: Existing run ID owned by the selected profile.
            status: Terminal persistent run status: completed, stopped or failed.

        Returns:
            None. Saves the ending account summary and the selected ended status.

        Raises:
            ContractError: If an identity, configuration or lifecycle precondition is invalid.

        Example:
            app.finish_run(profile_id, plain_run_id, status='stopped')
        """
        return self._operations.finish_run(profile_id, run_id, status)

    def events(self, profile_id: str, account_id: str) -> List[JsonObject]:
        """Read every saved account-history event, from account opening onward.

        Use this to inspect or verify how the account reached its current state. The list
        includes activity from all of its runs, unlike run_events(), which selects one run.
        All events are loaded into memory in sequence order. Pass a complete starting portion
        of this list to replay_snapshot() to check a historical account state.

        Args:
            profile_id: Profile ID returned by create_profile(); selects whose data to access.
            account_id: Account ID returned by create_account(); it must belong to this profile.

        Returns:
            list[JsonObject]: All account events in ascending sequence, including opening and
            all runs.

        Raises:
            ContractError: If an identity, configuration or lifecycle precondition is invalid.

        Example:
            ledger = app.events(profile_id, account_id)
        """
        return self._operations.events(profile_id, account_id)

    def snapshot(self, profile_id: str, account_id: str) -> AccountSnapshot:
        """Read the account's current saved cash, holdings and orders.

        Use this to inspect the account before or after an experiment. The result describes
        committed account changes: trades that have not been committed are not included.
        It is a value for this call and will not update itself when later trades happen.

        Money uses integer USD ten-thousandths and quantities use shares. Holdings are not
        valued at the current market price here. This call also does not synchronize the
        account with a specific instant on the experiment clock.

        Args:
            profile_id: Profile ID returned by create_profile(); selects whose data to access.
            account_id: Account ID returned by create_account(); it must belong to this profile.

        Returns:
            AccountSnapshot: Frozen value with cash, positions, fees, realized_pnl, sequence and
            orders.

        Raises:
            ContractError: If an identity, configuration or lifecycle precondition is invalid.

        Example:
            account = app.snapshot(profile_id, account_id)
            cash_usd = account.cash / 10_000
        """
        return self._operations.snapshot(profile_id, account_id)

    def rebuild_account(self, profile_id: str, account_id: str) -> AccountSnapshot:
        """Repair an account's saved summary by recalculating it from its event history.

        Use this for account maintenance while no experiment owns the account. It writes
        the rebuilt summary and checkpoints back to storage. It does not create new trades,
        append financial events or call a strategy.

        For a read-only check of an event list, use replay_snapshot() instead.

        Args:
            profile_id: Profile ID returned by create_profile(); selects whose data to access.
            account_id: Account ID returned by create_account(); it must belong to this profile.

        Returns:
            AccountSnapshot: The recalculated account state, after its saved summary
            has been updated.

        Raises:
            ContractError: If an identity, configuration or lifecycle precondition is invalid.

        Example:
            rebuilt = app.rebuild_account(profile_id, account_id)
        """
        return self._operations.rebuild_account(profile_id, account_id)

    def replay_snapshot(
        self, profile_id: str, account_id: str, events: List[JsonObject]
    ) -> AccountSnapshot:
        """Calculate what an account looked like after a supplied sequence of events.

        Use this to verify the account history or inspect an earlier balance. Supply a
        nonempty, uninterrupted sequence starting with this account's opening event. It
        may end before the latest saved event if you want an older state.

        The result is calculated without changing storage. No market playback, strategy,
        model or trading command is run.

        Args:
            profile_id: Profile ID returned by create_profile(); selects whose data to access.
            account_id: Account ID returned by create_account(); it must belong to this profile.
            events: Nonempty ordered ledger prefix for this account, starting at its opening
                event.

        Returns:
            AccountSnapshot: Cash, holdings and orders calculated from the supplied events.

        Raises:
            ContractError: If an identity, configuration or lifecycle precondition is invalid.

        Example:
            ledger = app.events(profile_id, account_id)
            reconstructed = app.replay_snapshot(profile_id, account_id, ledger)
        """
        return self._operations.replay_snapshot(profile_id, account_id, events)

    def _submit(
        self, profile_id: str, run_id: str, maker_id: str, intent: Intent, time_ns: int
    ) -> str:
        """Record a proposed action for a plain run after checking the maker's authority.

        This is the internal path used by DecisionSession.submit(). It saves an intent and
        returns its ID; it does not submit an order to a market or confirm a fill.

        Args:
            profile_id: Profile ID returned by create_profile(); selects whose data to access.
            run_id: Existing run ID owned by the selected profile.
            maker_id: Maker ID with current command authority for the run.
            intent: Validated Intent with integer share quantity and integer price in USD ten-
                thousandths.
            time_ns: Nonnegative integer simulation/session nanoseconds.

        Returns:
            str: Intent identity, not an executed market order or fill acknowledgement.

        Raises:
            ContractError: If an identity, configuration or lifecycle precondition is invalid.
        """
        return self._operations._submit(profile_id, run_id, maker_id, intent, time_ns)

    def _policy_state(self, profile_id: str, run_id: str, maker_id: str, value: JsonValue) -> None:
        """Save a strategy's JSON bookkeeping value for a plain run.

        This is used by DecisionSession.save_state(). The maker must still control the run.
        Policy-configured trading runs save their decision state through their runner instead.

        Args:
            profile_id: Profile ID returned by create_profile(); selects whose data to access.
            run_id: Existing run ID owned by the selected profile.
            maker_id: Maker ID with current command authority for the run.
            value: Finite JSON value to save as plain-run policy state.

        Returns:
            None. Configured decision runs must use their Runner journal.

        Raises:
            ContractError: If an identity, configuration or lifecycle precondition is invalid.
        """
        return self._operations._policy_state(profile_id, run_id, maker_id, value)

    def _fixture_event(
        self, profile_id: str, run_id: str, event_id: str, time_ns: int, kind: str, data: JsonObject
    ) -> None:
        """Append a known account event used by trusted fixtures and demonstrations.

        The operation checks the run, event time and caller authority before saving it.
        It applies only to plain runs; real financial execution runs reject these synthetic
        events and must use their normal order-processing path.

        Args:
            profile_id: Profile ID returned by create_profile(); selects whose data to access.
            run_id: Existing run ID owned by the selected profile.
            event_id: Unique event identity within the account ledger.
            time_ns: Nonnegative integer simulation/session nanoseconds.
            kind: Supported ledger fixture event kind; distinct from a maker kind.
            data: Finite JSON event payload.

        Returns:
            None. Financial execution runs reject fixture events.

        Raises:
            ContractError: If an identity, configuration or lifecycle precondition is invalid.
        """
        return self._operations._fixture_event(profile_id, run_id, event_id, time_ns, kind, data)

    def create_execution_run(
        self,
        profile_id: str,
        account_id: str,
        maker_id: str,
        symbol: str,
        config: Optional[ExecutionConfig] = None,
    ) -> str:
        """Save a run that will accept orders through a separate execution session.

        Use this for manual setup when a maker is already registered. It saves the account,
        one stock symbol and trading rules. The account must have no active run or open
        orders. config=None uses the default ExecutionConfig.

        The returned run ID is only a saved record. Next load a fresh market and call
        execution_session() to connect order processing. No market or process starts here.
        For ordinary app use, open_experiment() connects these parts for you.

        Args:
            profile_id: Profile ID returned by create_profile(); selects whose data to access.
            account_id: Account ID returned by create_account(); it must belong to this profile.
            maker_id: Maker ID with current command authority for the run.
            symbol: Stock symbol, such as AAPL. Trading experiments currently use one
                symbol per run; standalone playback selects the book for this symbol.
            config: ExecutionConfig, or None for defaults.

        Returns:
            str: Run ID with validated execution settings and database status="running".

        Raises:
            ContractError: If an identity, configuration or lifecycle precondition is invalid.

        Example:
            run_id = app.create_execution_run(profile_id, account_id, maker_id, 'AAPL')
            owner = app.execution_session(profile_id, run_id, maker_id, market)
        """
        return self._operations.create_execution_run(
            profile_id, account_id, maker_id, symbol, config
        )

    def create_decision_run(
        self,
        profile_id: str,
        account_id: str,
        maker_id: str,
        symbol: str,
        policy: Policy,
        config: Optional[DecisionConfig] = None,
        execution_config: Optional[ExecutionConfig] = None,
    ) -> str:
        """Save a run that will ask a policy to make trading decisions.

        Use this for manual setup with an existing account and maker. It records the policy
        description, decision settings and trading rules. config=None selects decision
        defaults for that policy kind; execution_config=None uses default trading rules.

        This returns a saved run ID, not a running policy. Next call decision_runner() with
        the same compatible policy and a fresh market. For ordinary app use, open_experiment()
        prepares the policy and trading components together.

        Args:
            profile_id: Profile ID returned by create_profile(); selects whose data to access.
            account_id: Account ID returned by create_account(); it must belong to this profile.
            maker_id: Maker ID with current command authority for the run.
            symbol: Stock symbol, such as AAPL. Trading experiments currently use one
                symbol per run; standalone playback selects the book for this symbol.
            policy: Strategy object implementing decisions.contracts.Policy. For an
                existing run, its identity and settings must match the saved policy.
            config: DecisionConfig, or None to select defaults for the policy kind.
            execution_config: Rules for order validation and fills. None uses the default
                ExecutionConfig.

        Returns:
            str: Run ID with policy/decision/execution settings saved; playback has not started.

        Raises:
            ContractError: If an identity, configuration or lifecycle precondition is invalid.

        Example:
            run_id = app.create_decision_run(profile_id, account_id, maker_id, 'AAPL', policy)
            runner = app.decision_runner(profile_id, run_id, maker_id, market, policy)
        """
        return self._operations.create_decision_run(
            profile_id, account_id, maker_id, symbol, policy, config, execution_config
        )
