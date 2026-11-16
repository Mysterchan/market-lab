"""Assemble one Agent runner from explicit Platform-owned capabilities.

No Platform instance enters this module. Assembly owns rollback until the
complete runner is returned; the controller then owns the runner.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional, Tuple, TYPE_CHECKING, Union
from .contracts import AgentAssemblyServices, AgentBinding, AgentLaunch
from sim_platform.domain.models import ContractError

if TYPE_CHECKING:
    from sim_platform.decisions.agent.runner import AgentDecisionRunner
    from sim_platform.market.prepared.session import PreparedSession
    from sim_platform.experiments.scheduling.clock import SharedExperimentClock
    from os import PathLike
    from pathlib import Path

    from sim_platform.agents.operation_log import AgentOperationLog
    from sim_platform.agents.tools.server.router import AgentTools
    from sim_platform.experiments.execution.session import ExperimentSession
    from sim_platform.experiments.scheduling.host import ExperimentRuntimeHandle
    from sim_platform.sdk import AgentFactory
from sim_platform.resources import cleanup_after_error

NS_PER_SECOND = 1_000_000_000


def require_bound_operation(payload: Any, position: Dict[str, str]) -> Any:
    """Setup cannot accept commands until bind_agent_tools installs the handler."""
    raise ContractError('agent tools are not bound')


def create_agent_pipeline(
    execution_factory: Callable[..., Any],
    compose_pipeline: Callable[..., ExperimentSession],
    profile_id: str,
    run_id: str,
    maker_id: str,
    market: PreparedSession,
    *,
    end_ns: int,
    lag_limit_ns: int,
    clock: SharedExperimentClock,
    capacity: int,
    start_ns: int,
    lag_window_ns: int,
) -> ExperimentSession:
    """Create execution before Tools binding; failed setup can still drain output."""
    from sim_platform.experiments.execution.market import LocalMarketExecution
    from .agent_driver import AgentPipelineOutput

    execution = execution_factory(profile_id, run_id, maker_id, market)
    try:
        pipeline = compose_pipeline(
            LocalMarketExecution(execution, market),
            market,
            end_ns=end_ns,
            lag_limit_ns=lag_limit_ns,
            sink=AgentPipelineOutput(),
            operation=require_bound_operation,
            clock=clock,
            capacity=capacity,
            start_ns=start_ns,
            lag_window_ns=lag_window_ns,
        )
    except Exception as exc:
        cleanup_after_error(exc, execution.close)
        raise
    pipeline.on_close(execution.close)
    return pipeline


def load_run_fundamentals(config: Dict[str, Any], reader: Any, symbol: str) -> Any:
    """Load the run's fundamentals file, or None when the run has none.

    A file or a folder of files, each read by market.fundamentals.load_fundamentals_path:
    EDGAR companyfacts JSON, a fundamentals CSV or an ALFRED vintage CSV. Publication dates are placed on the experiment
    timeline with the recording's first date, so the recording must carry dates.

    Raises:
        ContractError: If the recording has no calendar or the file is invalid.
    """
    path = config.get('fundamentals_path')
    if not path:
        return None
    from sim_platform.domain.models import ContractError
    from sim_platform.market.fundamentals import load_fundamentals_path

    calendar = (getattr(reader, 'manifest', None) or {}).get('calendar') or {}
    start = calendar.get('start_date')
    if not start:
        raise ContractError('fundamentals need a dated recording, such as a price-bar file')
    return load_fundamentals_path(path, symbol, start)


def load_run_documents(config: Dict[str, Any]) -> Any:
    """Load the run's documents file for documents.search/read, or None without one."""
    path = config.get('documents_path')
    if not path:
        return None
    from sim_platform.market.documents import load_documents

    return load_documents(path)


def bind_agent_tools(
    directory: Path,
    create_log: Callable[..., AgentOperationLog],
    archive_log: Callable[..., Any],
    commit_decision: Callable[..., Any],
    profile: str,
    experiment_id: str,
    binding: AgentBinding,
    pipeline: ExperimentSession,
    log_path: Optional[Union[str, PathLike[str]]],
    runtime: ExperimentRuntimeHandle,
) -> Tuple[AgentTools, AgentOperationLog]:
    """Wire read/trade capabilities and output at the pipeline setup boundary."""
    from sim_platform.agents.tools.server.router import AgentTools
    from sim_platform.agents.tools.trading.commands import TradingTools
    from .agent_driver import AgentToolCapture, AgentPipelineOutput

    if log_path is None:
        from sim_platform.persistence.agent_log_files import default_agent_log_path

        log_path = default_agent_log_path(directory, binding['run_id'])
    recorder = create_log(profile, experiment_id, binding['run_id'], log_path)
    queries = None
    try:
        from functools import partial
        from sim_platform.experiments.execution.market import ExecutionCommands

        commands = ExecutionCommands(commit_decision, profile, binding['run_id'])
        capture = AgentToolCapture(
            pipeline.driver, partial(commands.commit, binding['maker_id']), pipeline.require_owner
        )
        tool_binding = dict(binding['config'], work_id='work-1', epoch='1')
        output = AgentPipelineOutput(
            recorder, lambda path: archive_log(profile, binding['run_id'], path)
        )
        trading = TradingTools(
            tool_binding, capture, capture.submit, capture.cancel, lambda: pipeline.receipt_index
        )
        queries = AgentTools(tool_binding, capture, lambda: pipeline.receipt_index, trading)
        from .agent_driver import PreparedToolCapture

        queries.bind_market_capture(
            PreparedToolCapture(
                pipeline.reader,
                pipeline.driver.execution.snapshot,
                load_run_fundamentals(
                    binding['config'], pipeline.reader, binding['config']['symbol']
                ),
                load_run_documents(binding['config']),
            )
        )
        pipeline.bind_market_reads(queries.market_read)
        from .binding import RuntimeBinding

        controls = RuntimeBinding(runtime, pipeline, queries.binding, capture)
        queries.controls = controls
        pipeline.bind_lifecycle(controls)
        pipeline.bind_ready(
            operation=queries.dispatch, sink=output, cancel_queries=queries.cancel_pending
        )
        return queries, recorder
    except Exception as exc:
        cleanup_after_error(exc, *((queries.close,) if queries is not None else ()), recorder.close)
        raise


def bind_agent_host(
    runner: AgentDecisionRunner,
    tools: AgentTools,
    runtime: ExperimentRuntimeHandle,
    config: Dict[str, Any],
    agent_factory: Optional[AgentFactory] = None,
    launch: Optional[AgentLaunch] = None,
) -> None:
    """Bind program and participant gates; rollback the child if binding fails."""
    from sim_platform.agents.hosting.host import AgentProcessHost
    from sim_platform.agents.hosting.protocol import TransportLimits

    program_config = dict(
        run_id=runner.run_id, work_id='work-1', epoch='1', symbol=config['symbol']
    )
    integration = config['definition']['integration']
    if launch is not None:
        from sim_platform.agents.hosting.loader import ProjectFactory

        agent_factory = ProjectFactory(launch['project'])
    if integration != 'configured' and agent_factory is None:
        raise ContractError('custom Agent requires saved source or an explicit trusted factory')
    program_config['integration'] = integration
    program_config['template'] = dict(
        initial_prompt=config['definition']['initial_prompt'],
        parameters=config['definition']['parameters'],
        granted_tools=config['granted_tools'],
        limits=config['limits'],
        symbol=config['symbol'],
        # Present only for a run on several symbols, so one-symbol settings are unchanged.
        **({'symbols': list(config['symbols'])} if config.get('symbols') else {}),
    )
    host = AgentProcessHost(
        runner.request_tool,
        program_config,
        TransportLimits.from_configuration(config['limits']),
        request_work=runner.next_work,
        on_exit=runner.agent_exited,
        work_client=runtime.work_client(),
        local_model=True,
        agent_factory=agent_factory,
        launch=launch,
    )
    try:
        runner.bind_process(host)
        from sim_platform.experiments.execution.controls import ExperimentControls

        runner.bind_barrier(
            ExperimentControls(
                runner.pipeline,
                runtime,
                decision=host,
                tools=tools,
                record=lambda value: tools.controls.record_notice('control_ack', value),
            )
        )
    except Exception as exc:
        cleanup_after_error(exc, host.close)
        raise


def assemble_agent_runner(
    profile: str,
    binding: AgentBinding,
    market: PreparedSession,
    log_path: Optional[Union[str, PathLike[str]]],
    clock: Optional[SharedExperimentClock] = None,
    *,
    services: AgentAssemblyServices,
    agent_factory: Optional[AgentFactory] = None,
) -> AgentDecisionRunner:
    """Create owned children for one runner; never register children as roots."""
    from decimal import Decimal
    from sim_platform.decisions.agent.runner import AgentDecisionRunner

    pipeline = runner = None
    tools = recorder = runtime = launch = None
    try:
        config = binding['config']
        launch = (
            services.resolve_project(profile, config['definition'])
            if config['definition'].get('package') and agent_factory is None
            else None
        )
        if config.get('sandbox') is not None:
            from sim_platform.agents.hosting.processes.sandbox import resolve, validate

            chosen = validate(config['sandbox'])
            if chosen is not None:
                if launch is None:
                    raise ContractError('a sandbox runs an imported Agent project only')
                launch['sandbox'] = resolve(chosen)
        from sim_platform.experiments.scheduling.clock import SharedExperimentClock, session_gaps
        from dataclasses import asdict
        import multiprocessing

        # speed 'turn': turn-based, the market waits while the Agent works.
        paced = config.get('speed') == 'turn'
        if clock is None:
            clock = SharedExperimentClock(
                multiprocessing.get_context('spawn'),
                int(config['start_ns']),
                session_gaps(
                    market.reader.manifest, int(config['start_ns']), config.get('timeout_seconds')
                ),
                **({'paced': True} if paced else {}),
            )
        elif not isinstance(clock, SharedExperimentClock):
            raise ContractError('Agent runtime requires shared experiment clock')
        elif getattr(clock, 'paced', False) != paced:
            raise ContractError('a turn-based Agent needs a turn-based experiment clock')
        runtime = services.runtime_factory(
            config,
            clock=clock,
            prepared_artifact=market.reader.directory,
            prepared_news=[asdict(n) for n in getattr(market.reader, 'news', ())],
        )
        pipeline = create_agent_pipeline(
            services.execution_factory,
            services.compose_pipeline,
            profile,
            binding['run_id'],
            binding['maker_id'],
            market,
            start_ns=int(config['start_ns']),
            end_ns=int(config['end_ns']),
            lag_limit_ns=int(Decimal(config['lag']['threshold_seconds']) * NS_PER_SECOND),
            clock=clock,
            capacity=config['limits']['pending_requests'],
            lag_window_ns=int(Decimal(config['lag']['window_seconds']) * NS_PER_SECOND),
        )
        tools, recorder = bind_agent_tools(
            services.directory,
            services.create_log,
            services.archive_log,
            services.commit_decision,
            profile,
            binding['experiment_id'],
            binding,
            pipeline,
            log_path,
            runtime,
        )
        runner = AgentDecisionRunner(
            pipeline, tools, binding['run_id'], binding['experiment_id'], recorder
        )
        if paced:
            from .pacer import TurnPacer
            from sim_platform.experiments.scheduling.policy.schedule import seconds_ns

            controls = tools.controls
            runner.bind_pacer(
                TurnPacer(
                    clock,
                    pipeline,
                    runtime.snapshot,
                    lambda: controls.published_sequence,
                    seconds_ns(config['timeout_seconds']),
                )
            )
        from .presentation import AgentPresentation

        runner.bind_presentation(
            AgentPresentation(services.presentation_operations, profile, binding, pipeline)
        )
        bind_agent_host(runner, tools, runtime, config, agent_factory, launch)
        return runner
    except Exception as exc:
        actions = (
            (runner.close,)
            if runner is not None
            else tuple(
                resource.close
                for resource in (pipeline, tools, recorder, runtime)
                if resource is not None
            )
        )
        if launch is not None and (runner is None or runner.process_host is None):
            actions += (launch['workspace'].close,)
        cleanup_after_error(
            exc, *actions, lambda: services.failed_start(profile, binding['run_id'])
        )
        raise
