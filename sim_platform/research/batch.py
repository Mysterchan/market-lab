"""Independent equal-start comparisons through the same research controller."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, cast

from sim_platform.domain.models import ContractError
from sim_platform.domain.json_values import copy_json
from sim_platform.decisions.policy.contracts import make_policy
from sim_platform.decisions.policy.models import DEFAULT_HOLD_NS, DEFAULT_RULE_QUANTITY

if TYPE_CHECKING:  # Annotation only.
    from sim_platform.decisions.policy.runner import PolicyDecisionRunner
    from sim_platform.market.native import PathInput
    from sim_platform.market.prepared.session import PreparedSession
    from .jobs import Cancellation
    from .controller import Preparation
    from .policy_controller import PolicyResearchController
    from .reports import ExportWriter, Report

# Defaults for one batch evaluation.
DEFAULT_SYMBOL = 'AAPL'
DEFAULT_CASH = 100_000_000  # money-scale units
DEFAULT_FEE_PER_SHARE = 100  # money-scale units
# Evaluation progress is reported once every this many ticks.
PROGRESS_TICK_INTERVAL = 256
# A comparison runs between 1 and this many configurations.
MAX_COMPARISON_CONFIGURATIONS = 20
# Hold durations compared when the caller gives no configurations.
ONE_SECOND_NS = 1_000_000_000

# Receives {'stage', 'frames', 'time_ns'} while a batch prepares or evaluates.
BatchProgress = Callable[[Dict[str, Any]], None]
# One comparison entry: optional 'name', 'policy', 'quantity' and 'hold_ns'.
ComparisonConfiguration = Dict[str, Any]
# Runs one evaluation: evaluator(path, cancellation=..., progress=..., include_details=..., **settings).
Evaluator = Callable[..., Optional['Report']]


def _evaluate(
    controller_factory: Callable[[], PolicyResearchController],
    path: PathInput,
    *,
    symbol: str = DEFAULT_SYMBOL,
    news: Optional[PathInput] = None,
    policy: str = 'rule',
    quantity: int = DEFAULT_RULE_QUANTITY,
    hold_ns: int = DEFAULT_HOLD_NS,
    cash: int = DEFAULT_CASH,
    fee_per_share: int = DEFAULT_FEE_PER_SHARE,
    latency_ns: int = 0,
    library: Optional[PathInput] = None,
    allow_implicit_eof: bool = False,
    cancellation: Optional[Cancellation] = None,
    progress: Optional[BatchProgress] = None,
    include_details: bool = True,
    start_mode: str = 'beginning',
    start_time_ns: Optional[int] = None,
    model: Optional[Dict[str, Any]] = None,
    book: Optional[Dict[str, int]] = None,
    allow_short: bool = False,
    profile: Optional[str] = None,
    end_time_ns: Optional[int] = None,
) -> Optional[Report]:
    """Run one automatic policy on its own new account; None if cancelled during preparation.

    ``profile`` puts that account in an existing profile, so the run can be evaluated
    with the profile's other runs (Platform.evaluation); by default a new profile is made.
    ``end_time_ns`` stops the run at the first market event at or after that time, as an
    Agent run stops at its chosen end; it is saved with the run's source.

    ``model`` holds the model method's settings (configuration.MODEL_DEFAULTS keys) and
    is used only with policy='model'. ``book`` sets a price-bar recording's synthetic
    spread and depth (market.prepared.bars.book_options).
    """
    if policy == 'human':
        raise ContractError('batch evaluation requires a rule policy')
    if policy == 'model':
        from .configuration import MODEL_DEFAULTS, model_settings

        if not isinstance(model, dict) or set(model) - set(MODEL_DEFAULTS):
            raise ContractError('the model policy needs model settings')
        model = model_settings(dict(MODEL_DEFAULTS, **model))
    else:
        make_policy(policy, quantity, hold_ns)
    controller = controller_factory()
    try:
        if profile is None:
            profile = controller.create_profile('Batch research')
        account = controller.create_account(profile, 'Independent account', cash)
        controller.begin_prepare(
            path, symbol, news, allow_implicit_eof, start_mode, start_time_ns, book=book
        )
        # begin_prepare set the preparation and the market it prepares.
        while cast('Preparation', controller.preparation)['status'] == 'preparing':
            if cancellation is not None and cancellation.cancelled:
                controller.cancel_preparation()
                return None
            controller.prepare_tick()
            if progress is not None:
                progress(
                    dict(
                        stage='preparation',
                        frames=cast('PreparedSession', controller.market).snapshot.sequence,
                        time_ns=cast('PreparedSession', controller.market).time_ns,
                    )
                )
        if end_time_ns is not None:
            # Saved with the run so evaluations can compare its market-time window.
            cast(Dict[str, Any], controller.source)['end_time_ns'] = int(end_time_ns)
        controller.start(
            profile,
            account,
            policy,
            quantity,
            hold_ns,
            fee_per_share,
            latency_ns,
            execution_mode='controlled',
            model=model,
            allow_short=allow_short,
        )
        controller.play()
        ticks = 0
        # start() bound a controlled-mode PolicyDecisionRunner.
        while cast('PolicyDecisionRunner', controller.runner).snapshot.status == 'running':
            if cancellation is not None and cancellation.cancelled:
                controller.stop()
                break
            if (
                end_time_ns is not None
                and cast('PreparedSession', controller.market).time_ns >= end_time_ns
            ):
                controller.stop()
                break
            controller.tick(render=False)
            ticks += 1
            if progress is not None and ticks % PROGRESS_TICK_INTERVAL == 0:
                progress(
                    dict(
                        stage='evaluation',
                        frames=cast('PreparedSession', controller.market).snapshot.sequence,
                        time_ns=cast('PreparedSession', controller.market).time_ns,
                    )
                )
        if include_details:
            return controller.report()
        from .reports import report_view

        report = report_view(controller.app, profile, cast(str, controller.run_id))
        return dict(
            format=report['format'],
            run={'id': controller.run_id, 'profile_id': profile},
            source=report['source'],
            summary=report['summary'],
        )
    finally:
        controller.close()


# How often an Agent evaluation checks whether its run has ended (seconds).
AGENT_POLL_SECONDS = 0.05
# A suite entry's Agent model: ModelProfile fields.
MODEL_FIELDS = {
    'provider',
    'model',
    'credential_ref',
    'network_timeout_seconds',
    'max_output_tokens',
    'temperature',
    'base_url',
}


def _evaluate_agent(
    platform: Any,
    path: PathInput,
    *,
    profile: str,
    agent_id: str,
    revision: int,
    settings: Dict[str, Any],
    model: Dict[str, Any],
    symbol: str = DEFAULT_SYMBOL,
    cash: int = DEFAULT_CASH,
    fee_per_share: int = DEFAULT_FEE_PER_SHARE,
    latency_ns: int = 0,
    model_provider: Optional[Callable[[], Any]] = None,
    cancellation: Optional[Cancellation] = None,
    progress: Optional[BatchProgress] = None,
    include_details: bool = True,
    preparation_lock: Optional[Any] = None,
    **_: Any,
) -> Optional[Report]:
    """Run a saved Agent revision once, in real time, on its own new account.

    The Agent trades from settings['start_ns'] to settings['end_ns'] at 1x: market time
    passes as wall-clock time passes, so a run takes as long as its window. ``model``
    holds ModelProfile fields; its credential must already be stored on the Platform.
    ``model_provider`` returns a fresh trusted provider per run (None: the default
    adapter). ``preparation_lock`` (a threading lock) makes concurrent runs prepare their
    market one at a time, since a workspace prepares one market at once; they then play
    together. Arguments meant for Rule and model-file runs are ignored. Returns the full
    report, or None if cancelled.
    """
    from contextlib import nullcontext
    import time

    from sim_platform.execution import ExecutionConfig
    from sim_platform.models.contracts import ModelProfile

    account = platform.create_account(profile, 'Independent account', cash)
    with preparation_lock if preparation_lock is not None else nullcontext():
        experiment = platform.open_experiment(
            profile,
            account,
            source=path,
            method='agent',
            agent_id=agent_id,
            revision=revision,
            settings=dict(settings, symbol=symbol),
            execution_config=ExecutionConfig(fee_per_share=fee_per_share, latency_ns=latency_ns),
            execution_mode='live',
        )
    try:
        platform.configure_experiment_model(
            experiment,
            ModelProfile(**model),
            provider=model_provider() if model_provider is not None else None,
        )
        experiment.play()
        while experiment.runner is not None and experiment.runner.active:
            if cancellation is not None and cancellation.cancelled:
                experiment.stop()
                return None
            experiment.tick(render=False)
            if progress is not None:
                progress(dict(stage='agent', frames=0, time_ns=experiment.market.time_ns))
            time.sleep(AGENT_POLL_SECONDS)
        # The market tail is saved only once the runner reports it ready; wait for it so
        # the report has its bars and closes.
        while experiment.runner is not None and not experiment.runner.archive_ready:
            experiment.tick(render=False)
            time.sleep(AGENT_POLL_SECONDS)
        experiment.tick(render=False)
        report = experiment.report()
        from .evaluation import agent_model_usage

        report['model_usage'] = agent_model_usage(
            platform.agent_log_archive(profile, experiment.run_id)
        )
        if include_details:
            return report
        return dict(
            format=report['format'],
            run={'id': experiment.run_id, 'profile_id': profile},
            source=report['source'],
            summary=report['summary'],
        )
    finally:
        experiment.close()


def _compare(
    path: PathInput,
    configurations: Optional[List[ComparisonConfiguration]] = None,
    *,
    evaluator: Evaluator,
    cancellation: Optional[Cancellation] = None,
    progress: Optional[BatchProgress] = None,
    include_details: bool = True,
    **common: Any,
) -> Report:
    """Evaluate each configuration on its own account and collect a comparison report."""
    configurations = (
        [
            {'name': 'Hold 1 second', 'hold_ns': ONE_SECOND_NS},
            {'name': 'Hold 2 seconds', 'hold_ns': 2 * ONE_SECOND_NS},
        ]
        if configurations is None
        else configurations
    )
    if (
        not isinstance(configurations, list)
        or not 1 <= len(configurations) <= MAX_COMPARISON_CONFIGURATIONS
    ):
        raise ContractError('comparison requires 1..20 configurations')
    allowed = {'name', 'policy', 'quantity', 'hold_ns'}
    configurations = cast(List[ComparisonConfiguration], copy_json(configurations))
    for config in configurations:
        if not isinstance(config, dict) or set(config) - allowed:
            raise ContractError('invalid comparison configuration')
        if 'name' in config:
            from sim_platform.domain.models import label

            label(config['name'], 'comparison name')
        policy = config.get('policy', common.get('policy', 'rule'))
        if policy == 'human':
            raise ContractError('batch evaluation requires a rule policy')
        make_policy(
            policy,
            config.get('quantity', common.get('quantity', DEFAULT_RULE_QUANTITY)),
            config.get('hold_ns', common.get('hold_ns', DEFAULT_HOLD_NS)),
        )
    results: List[Dict[str, Any]] = []
    for config in configurations:
        if cancellation is not None and cancellation.cancelled:
            break
        arguments = {**common, **{k: v for k, v in config.items() if k != 'name'}}
        report = evaluator(
            path,
            cancellation=cancellation,
            progress=progress,
            include_details=include_details,
            **arguments,
        )
        if report is None and cancellation is not None and cancellation.cancelled:
            break
        results.append(
            # report is None only after cancellation, which ends the loop above.
            dict(
                name=config.get('name', 'Rule'),
                summary=cast('Report', report)['summary'],
                report=report,
            )
        )
    settings = {
        k: str(v) if k in ('news', 'library') and v is not None else v for k, v in common.items()
    }
    return copy_json(
        dict(
            format='trading-comparison-v1',
            status=(
                'cancelled' if cancellation is not None and cancellation.cancelled else 'completed'
            ),
            pending_configurations=configurations[len(results) :],
            experiment='independent equal-start accounts; no handoff',
            common_settings=settings,
            configurations=configurations,
            results=results,
        )
    )


# One suite entry: 'name', 'policy' ('rule', 'model' or 'agent') and 'repetitions'. A
# rule entry may give 'quantity' and 'hold_ns'; a model entry gives 'model' (model-file
# settings); an Agent entry gives 'agent_id', 'revision', 'settings' (Agent experiment
# settings with 'start_ns' and 'end_ns') and 'model' (ModelProfile fields).
SuiteEntry = Dict[str, Any]
# Most recordings one suite evaluates.
MAX_SUITE_RECORDINGS = 20
# Most repetitions of one entry on one recording.
MAX_REPETITIONS = 200
# Repetitions of an Agent entry unless given: its model answers differently each run.
DEFAULT_AGENT_REPETITIONS = 10
# Most Agent runs a suite plays at the same time.
MAX_CONCURRENCY = 16


def _check_suite_entries(entries: List[SuiteEntry]) -> List[SuiteEntry]:
    from .evaluation import MAX_ENTRIES

    if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_ENTRIES:
        raise ContractError(f'a suite needs 1..{MAX_ENTRIES} entries')
    allowed = {'name', 'policy', 'quantity', 'hold_ns', 'model', 'repetitions'}
    agent_keys = {'name', 'policy', 'agent_id', 'revision', 'settings', 'model', 'repetitions'}
    entries = cast(List[SuiteEntry], copy_json(entries))
    names = set()
    for entry in entries:
        if not isinstance(entry, dict) or 'name' not in entry:
            raise ContractError('each suite entry is a dict with a name')
        policy = entry.get('policy', 'rule')
        if set(entry) - (agent_keys if policy == 'agent' else allowed):
            raise ContractError(f'entry {entry["name"]!r} has keys its policy does not use')
        if entry['name'] in names:
            raise ContractError(f'entry name {entry["name"]!r} is used twice')
        names.add(entry['name'])
        if policy == 'agent':
            entry.setdefault('repetitions', DEFAULT_AGENT_REPETITIONS)
            settings, model = entry.get('settings'), entry.get('model')
            if (
                not isinstance(entry.get('agent_id'), str)
                or not isinstance(entry.get('revision'), int)
                or not isinstance(settings, dict)
                or 'start_ns' not in settings
                or 'end_ns' not in settings
            ):
                raise ContractError(
                    'an Agent entry needs agent_id, revision and settings with start_ns and end_ns'
                )
            if not isinstance(model, dict) or set(model) - MODEL_FIELDS:
                raise ContractError('an Agent entry needs model: ModelProfile fields')
        elif policy not in ('rule', 'model'):
            raise ContractError('suite entries use the rule, model or agent policy')
        repetitions = entry.get('repetitions', 1)
        if (
            not isinstance(repetitions, int)
            or isinstance(repetitions, bool)
            or not 1 <= repetitions <= MAX_REPETITIONS
        ):
            raise ContractError(f'repetitions must be 1..{MAX_REPETITIONS}')
    return entries


def _evaluate_suite(
    paths: List[PathInput],
    entries: List[SuiteEntry],
    *,
    evaluator: Evaluator,
    agent_evaluator: Optional[Evaluator] = None,
    baseline: Optional[str] = None,
    metric: str = 'total_return',
    seed: int = 2026,
    resamples: int = 10_000,
    cancellation: Optional[Cancellation] = None,
    progress: Optional[BatchProgress] = None,
    model_provider: Optional[Callable[[], Any]] = None,
    training_cutoffs: Optional[Dict[str, str]] = None,
    concurrency: int = 1,
    token_prices: Optional[Dict[str, float]] = None,
    trials: Optional[Dict[str, int]] = None,
    **common: Any,
) -> Report:
    """Run every entry on every recording on new accounts, then evaluate each recording.

    ``trials`` maps entry names to how many configurations were tried (for the deflated
    Sharpe ratio); sweeps pass their number of combinations.

    With Agent entries, every entry trades the Agents' window: Rule and model-file runs
    start at its start_ns and stop at its end_ns, so all runs cover the same market time.
    ``concurrency`` plays up to that many runs of one Agent entry at the same time, each
    still in real time on its own account; it shortens the wait, not any run.
    """
    from functools import partial
    from pathlib import Path
    from threading import Lock

    from .evaluation import evaluate_reports, memorisation_flags

    if not isinstance(paths, list) or not 1 <= len(paths) <= MAX_SUITE_RECORDINGS:
        raise ContractError(f'a suite needs a list of 1..{MAX_SUITE_RECORDINGS} recordings')
    entries = _check_suite_entries(entries)
    if baseline is not None and baseline not in {e['name'] for e in entries}:
        raise ContractError(f'baseline {baseline!r} is not one of the entries')
    if (
        not isinstance(concurrency, int)
        or isinstance(concurrency, bool)
        or not 1 <= concurrency <= MAX_CONCURRENCY
    ):
        raise ContractError(f'concurrency must be 1..{MAX_CONCURRENCY}')
    cutoffs = training_cutoffs or {}
    if not isinstance(cutoffs, dict) or set(cutoffs) - {e['name'] for e in entries}:
        raise ContractError('training_cutoffs must map entry names to dates')
    agents = [e for e in entries if e.get('policy') == 'agent']
    window: Dict[str, Any] = {}
    if agents:
        if agent_evaluator is None:
            raise ContractError('this batch cannot run Agents; open it with Platform.open_batch()')
        if not common.get('profile'):
            raise ContractError('Agent entries need the profile that holds the saved Agents')
        windows = {(str(e['settings']['start_ns']), str(e['settings']['end_ns'])) for e in agents}
        if len(windows) != 1:
            raise ContractError('every Agent entry must use the same start_ns and end_ns')
        start, end = (int(v) for v in windows.pop())
        window = dict(start_mode='custom', start_time_ns=start, end_time_ns=end)
        # The Agent saves its source's resolved path; give every run the same one.
        paths = [str(Path(p).expanduser().resolve()) for p in paths]
    recordings: List[Dict[str, Any]] = []
    cancelled = False
    preparation_lock = Lock()
    for path in paths:
        reports: Dict[str, List[Report]] = {}
        for entry in entries:
            settings = {
                k: v for k, v in entry.items() if k not in ('name', 'repetitions', 'policy')
            }
            agent = entry.get('policy') == 'agent'
            repetitions = entry.get('repetitions', 1)
            if agent:
                run = partial(
                    cast(Evaluator, agent_evaluator),
                    path,
                    cancellation=cancellation,
                    progress=progress,
                    include_details=True,
                    model_provider=model_provider,
                    preparation_lock=preparation_lock,
                    **{**common, **settings},
                )
            else:
                run = partial(
                    evaluator,
                    path,
                    cancellation=cancellation,
                    progress=progress,
                    include_details=True,
                    **{**window, **common, 'policy': entry.get('policy', 'rule'), **settings},
                )
            # Rule and model-file runs are synchronous and quick; only Agents run together.
            runs = _run_repetitions(run, repetitions, concurrency if agent else 1, cancellation)
            finished = [report for report in runs if report is not None]
            if finished:
                reports.setdefault(entry['name'], []).extend(finished)
            if len(finished) < repetitions:
                cancelled = True
            if cancelled:
                break
        if cancelled:
            break
        recordings.append(
            dict(
                path=str(path),
                evaluation=evaluate_reports(
                    reports,
                    baseline=baseline,
                    metric=metric,
                    seed=seed,
                    resamples=resamples,
                    token_prices=token_prices,
                    trials=trials,
                    flags={
                        report['run']['id']: memorisation_flags(report, cutoffs.get(name))
                        for name, group in reports.items()
                        for report in group
                    },
                ),
            )
        )
    return copy_json(
        dict(
            format='agent-evaluation-suite-v1',
            status='cancelled' if cancelled else 'completed',
            entries=entries,
            common_settings={
                k: str(v) if k in ('news', 'library') and v is not None else v
                for k, v in common.items()
            },
            concurrency=concurrency,
            recordings=recordings,
        )
    )


def _run_repetitions(
    run: Callable[[], Optional[Report]],
    repetitions: int,
    concurrency: int,
    cancellation: Optional[Cancellation],
) -> List[Optional[Report]]:
    """Call ``run`` ``repetitions`` times, at most ``concurrency`` at once, in order.

    A None result (cancelled) or a cancellation stops further runs from starting; runs
    already playing finish or stop through their own cancellation check. The first
    error is raised after every started run has ended.
    """
    if concurrency == 1:
        results: List[Optional[Report]] = []
        for _ in range(repetitions):
            if cancellation is not None and cancellation.cancelled:
                break
            report = run()
            results.append(report)
            if report is None:
                break
        return results
    from concurrent.futures import ThreadPoolExecutor

    def guarded() -> Optional[Report]:
        if cancellation is not None and cancellation.cancelled:
            return None
        return run()

    with ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix='agent-run') as pool:
        futures = [pool.submit(guarded) for _ in range(repetitions)]
    return [future.result() for future in futures]


class ResearchBatch:
    """Batch workflows use platform-configured controller factories, without opening a Platform.

    Platform.open_batch creates and registers it. Each evaluation opens and closes its
    own controller through ``controller_factory``; the batch itself owns nothing else.
    close() (or leaving a ``with`` block) marks it closed and runs the on_close callback.
    """

    def __init__(
        self,
        controller_factory: Callable[[], PolicyResearchController],
        directory: Path,
        *,
        export_writer: ExportWriter,
        agent_evaluator: Optional[Evaluator] = None,
    ) -> None:
        self._controller_factory = controller_factory
        self._agent_evaluator = agent_evaluator
        self.directory = directory
        self._export_writer = export_writer
        self._closed = False
        self._on_close: Callable[[], None] = lambda: None

    def on_close(self, callback: Callable[[], None]) -> None:
        self._on_close = callback

    def close(self) -> None:
        self._closed = True
        self._on_close()

    def __enter__(self) -> ResearchBatch:
        if self._closed:
            raise ContractError('research batch is closed')
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def evaluate(self, path: PathInput, **arguments: Any) -> Optional[Report]:
        if self._closed:
            raise ContractError('research batch is closed')
        return _evaluate(self._controller_factory, path, **arguments)

    def compare(
        self,
        path: PathInput,
        configurations: Optional[List[ComparisonConfiguration]] = None,
        **arguments: Any,
    ) -> Report:
        if self._closed:
            raise ContractError('research batch is closed')
        return _compare(path, configurations, evaluator=self.evaluate, **arguments)

    def evaluate_suite(
        self, paths: List[PathInput], entries: List[SuiteEntry], **arguments: Any
    ) -> Report:
        """Run each entry ``repetitions`` times per recording and evaluate each recording.

        Every run gets its own new account. Arguments not named here (symbol, cash,
        fee_per_share, profile, ...) apply to every run. Agent entries run in real time
        over their window and need ``profile``; ``model_provider`` optionally returns a
        trusted model provider for each Agent run. Returns an
        'agent-evaluation-suite-v1' report with one evaluation per recording; see
        docs/user-guide/evaluation.md.
        """
        if self._closed:
            raise ContractError('research batch is closed')
        return _evaluate_suite(
            paths,
            entries,
            evaluator=self.evaluate,
            agent_evaluator=self._agent_evaluator,
            **arguments,
        )

    def evaluate_benchmark(
        self, folder: PathInput, entries: List[SuiteEntry], **arguments: Any
    ) -> Report:
        """Run entries against the fixed benchmark baselines on its synthetic regimes.

        Writes the reproducible benchmark recordings into ``folder`` and evaluates every
        entry against buy and hold, a one-day rule and a moving-average model; see
        research.benchmark and docs/user-guide/evaluation.md#benchmark.
        """
        if self._closed:
            raise ContractError('research batch is closed')
        from .benchmark import evaluate_benchmark

        return evaluate_benchmark(self, folder, entries, **arguments)

    def export(self, report: Report, path: PathInput) -> str:
        if self._closed:
            raise ContractError('research batch is closed')
        from .reports import export_report

        return export_report(report, path, writer=self._export_writer)


def _borrow_batch(platform: Any, library: Optional[PathInput]) -> ResearchBatch:
    """Open a batch on the caller's Platform (application package, typed later)."""
    from sim_platform import Platform

    if not isinstance(platform, Platform):
        raise ContractError('batch entry requires an existing Platform; use Platform.open_batch()')
    return platform.open_batch(library=library)


def evaluate(platform: Any, path: PathInput, **arguments: Any) -> Optional[Report]:
    """Legacy name; caller supplies and owns the Platform."""
    library = arguments.pop('library', None)
    with _borrow_batch(platform, library) as batch:
        return batch.evaluate(path, **arguments)


def compare(
    platform: Any,
    path: PathInput,
    configurations: Optional[List[ComparisonConfiguration]] = None,
    **arguments: Any,
) -> Report:
    """Legacy name; independent runs share the caller's Platform."""
    library = arguments.pop('library', None)
    with _borrow_batch(platform, library) as batch:
        return batch.compare(path, configurations, **arguments)
