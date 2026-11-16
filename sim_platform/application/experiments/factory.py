"""Platform-owned composition for live Human/Rule experiments.

Factories are injected; lower layers never construct Platform or register roots.
The assembly function owns rollback until it returns the complete runner.
"""

from __future__ import annotations

import multiprocessing
from typing import TYPE_CHECKING, Any, Callable, Dict, Optional, Union

from sim_platform.resources import cleanup_after_error
from sim_platform.decisions.policy.live.runner import LivePolicyDecisionRunner
from sim_platform.decisions.policy.live.owner import PolicyOwner, ObservedMarketExecution
from sim_platform.decisions.policy.live.program import TrustedPolicyProgram
from sim_platform.experiments.scheduling.clock import SharedExperimentClock, session_gaps
from sim_platform.experiments.scheduling.host import ExperimentRuntimeHandle
from sim_platform.experiments.recording import ExperimentLog
from sim_platform.experiments.execution.controls import ExperimentControls
from .runtime_policy import PreparedWorkPolicy
from sim_platform.agents.hosting.host import AgentProcessHost
from sim_platform.agents.hosting.protocol import TransportLimits
from sim_platform.persistence.agent_log_files import AgentLogFile, default_agent_log_path
from .binding import RuntimeBinding

if TYPE_CHECKING:  # Annotation only.
    from os import PathLike

    from sim_platform.decisions.policy.contracts import Policy
    from sim_platform.decisions.policy.models import DecisionConfig
    from sim_platform.decisions.policy.runner import PolicyDecisionRunner
    from sim_platform.experiments.execution.session import ExperimentSession
    from sim_platform.market.prepared.session import PreparedSession

# Upper bound of a live policy session: the last nanosecond of the day.
SESSION_END_NS = 86_399_999_999_999
# Live policy Runtime settings.
EVENT_BUCKETS = 64
LAG_LIMIT_NS = 100_000_000
# A run fails only if execution stays more than LAG_LIMIT_NS behind live time for this
# long. A single stall (a cold first decision on a loaded machine) is tolerated; commands
# still apply at their stamped time against the exact market prefix, so lag never shows a
# decision later data.
LAG_WINDOW_NS = 1_000_000_000
# Rule child pipe limits: message bytes, pending bytes and pending messages.
RULE_TRANSPORT_LIMITS = (65536, 262144, 8)


def policy_configuration(market: PreparedSession, config: DecisionConfig) -> Dict[str, Any]:
    """Rule wake policy preserves trigger filtering and minimum decision interval.

    Human uses the same Runtime lifecycle but never requests decision work.
    Source EOF normally ends the session before its upper bound: midnight of the first
    day, or the end of the recording when it covers more than one day.
    """
    return dict(
        start_ns=getattr(market, 'origin_ns', market.time_ns),
        end_ns=max(SESSION_END_NS, market.reader.manifest['last_ns'] + 1),
        symbol=market.snapshot.symbol,
        event_buckets=EVENT_BUCKETS,
        minimum_interval_ns=config.interval_ns,
        timeout_seconds='30',
        retry=dict(
            max_attempts=1,
            format_max_attempts=1,
            retry_classes=[],
            base_delay_seconds='0.1',
            max_delay_seconds='1',
        ),
        schedule=dict(
            mode='event',
            busy_event_policy='wake_on_pending_event',
            events=(
                []
                if config.mode == 'manual'
                else [dict(kind='market_update', symbol=market.snapshot.symbol)]
            ),
        ),
    )


def new_rule_host(
    policy: Policy,
    config: DecisionConfig,
    pipeline: ExperimentSession,
    runtime: ExperimentRuntimeHandle,
    lifecycle: RuntimeBinding,
    run_id: str,
) -> Optional[AgentProcessHost]:
    """Reuse the Agent host lifecycle with a trusted policy-specific program."""
    if policy.kind == 'human':
        return None
    return AgentProcessHost(
        pipeline.submit,
        dict(run_id=run_id),
        TransportLimits(*RULE_TRANSPORT_LIMITS),
        program=TrustedPolicyProgram(policy, config),
        work_client=runtime.work_client(),
        on_exit=lambda state, reason: (
            lifecycle.agent_failed(reason or 'rule exited') if state == 'failed' else None
        ),
    )


def assemble_live_policy(
    new_core: Callable[[str, str, str, PreparedSession, Policy], PolicyDecisionRunner],
    directory: Union[str, PathLike[str]],
    profile: str,
    run_id: str,
    maker: str,
    market: PreparedSession,
    policy: Policy,
    *,
    pipeline_factory: Callable[..., ExperimentSession],
) -> LivePolicyDecisionRunner:
    """Compose one Session, Live Runtime, log and optional Rule carrier.

    Every constructed child remains owned during setup, including failures.
    The caller registers only the returned experiment controller root.
    """
    core = new_core(profile, run_id, maker, market, policy)
    runtime = pipeline = host = recorder = None
    try:
        config = policy_configuration(market, core.config)
        origin = getattr(market, 'origin_ns', market.time_ns)
        clock = SharedExperimentClock(
            multiprocessing.get_context('spawn'),
            origin,
            # A manual experiment requests no decision work, so nothing can be in
            # progress at the close and no extra waiting is needed before a skip.
            session_gaps(
                market.reader.manifest,
                origin,
                config['timeout_seconds'] if config['schedule']['events'] else None,
            ),
        )
        config['prepared_artifact'] = str(market.reader.directory)
        from dataclasses import asdict

        config['prepared_news'] = [asdict(n) for n in getattr(market.reader, 'news', ())]
        runtime = ExperimentRuntimeHandle(PreparedWorkPolicy, config, clock=clock)
        owner = PolicyOwner(core)
        core.bind_observations(owner.observation)
        owner.publish(None)
        recorder = ExperimentLog(AgentLogFile(default_agent_log_path(directory, run_id)), run_id)
        pipeline = pipeline_factory(
            ObservedMarketExecution(core.execution, market, owner.observe_event, owner.publish),
            market,
            clock=clock,
            end_ns=config['end_ns'],
            lag_limit_ns=LAG_LIMIT_NS,
            lag_window_ns=LAG_WINDOW_NS,
            sink=recorder,
            operation=owner.perform,
        )
        lifecycle = RuntimeBinding(
            runtime, pipeline, dict(run_id=run_id, symbol=config['symbol']), owner.capture
        )
        pipeline.bind_lifecycle(lifecycle)
        host = new_rule_host(policy, core.config, pipeline, runtime, lifecycle, run_id)
        runner = LivePolicyDecisionRunner(core, pipeline, runtime, owner, host, recorder)
        runner.barrier = ExperimentControls(
            pipeline,
            runtime,
            decision=host,
            record=lambda value: lifecycle.record_notice('control_ack', value),
        )
        return runner
    except Exception as exc:
        cleanup_after_error(
            exc, *(r.close for r in (pipeline, host, runtime, core, recorder) if r is not None)
        )
        raise
