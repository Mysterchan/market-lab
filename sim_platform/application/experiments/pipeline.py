"""Prepared financial lifecycle assembly, replay seed and terminal evidence."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Union
from sim_platform.application.api_types import JsonObject
from sim_platform.domain.models import ContractError

if TYPE_CHECKING:
    from sim_platform.experiments.scheduling.clock import ExperimentClock, SharedExperimentClock
    from sim_platform.experiments.execution.market import LocalMarketExecution
    from sim_platform.experiments.execution.session import ExperimentSession
    from sim_platform.market.prepared.session import PreparedSession


def assemble_pipeline(
    driver: LocalMarketExecution,
    market: PreparedSession,
    *,
    run_events: Callable[[str, str], List[JsonObject]],
    end_ns: int,
    sink: Callable[[JsonObject], None],
    operation: Callable[[Any, Dict[str, str]], Any],
    lag_limit_ns: int,
    clock: Optional[Union[ExperimentClock, SharedExperimentClock]] = None,
    start_ns: Optional[int] = None,
    capacity: int = 64,
    lag_window_ns: int = 0,
) -> ExperimentSession:
    """Build a ready Session; driver/market are borrowed and caller owns rollback."""
    from dataclasses import asdict
    from sim_platform.market.prepared.session import PreparedSession
    from sim_platform.experiments.execution.session import ExperimentSession
    from sim_platform.experiments.scheduling.clock import ExperimentClock, session_gaps

    from sim_platform.market.prepared.merged import MergedMarket

    if not isinstance(market, (PreparedSession, MergedMarket)):
        raise ContractError('background experiments require a prepared research source')
    start = market.origin_ns if start_ns is None else start_ns
    end = min(end_ns, market.reader.manifest['last_ns'] + 1)
    execution = driver.execution.snapshot
    from sim_platform.replay.comparison import financial_events

    def terminal_evidence() -> Dict[str, Any]:
        return dict(
            events=financial_events(run_events(execution.account.profile_id, execution.run_id))
        )

    return ExperimentSession(
        driver,
        market.reader,
        clock=clock or ExperimentClock(start, gaps=session_gaps(market.reader.manifest, start)),
        start_ns=start,
        end_ns=end,
        sink=sink,
        operation=operation,
        capacity=capacity,
        lag_limit_ns=lag_limit_ns,
        lag_window_ns=lag_window_ns,
        terminal_evidence=terminal_evidence,
        replay_seed=dict(
            artifact=str(market.reader.directory),
            **(
                dict(artifacts=[str(d) for d in market.reader.directories])
                if isinstance(market, MergedMarket)
                else {}
            ),
            start_ns=start,
            end_ns=end,
            execution=asdict(execution.config),
            initial_account=asdict(execution.account),
            publications=[asdict(n) for n in getattr(market.reader, 'news', ())],
        ),
    )
