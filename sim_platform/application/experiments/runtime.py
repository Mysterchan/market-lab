"""Validated Agent scheduling configuration and child assembly; caller owns the handle."""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Optional, Sequence, Union
from sim_platform.application.api_types import JsonObject
from sim_platform.domain.models import ContractError

if TYPE_CHECKING:
    from os import PathLike
    from sim_platform.experiments.scheduling.clock import SharedExperimentClock
    from sim_platform.experiments.scheduling.host import ExperimentRuntimeHandle

    PathInput = Union[str, PathLike[str]]


def assemble_runtime(
    configuration: JsonObject,
    *,
    clock: Optional[SharedExperimentClock] = None,
    on_notice: Optional[Callable[[str, JsonObject], None]] = None,
    on_failure: Optional[Callable[[Exception], None]] = None,
    prepared_artifact: Optional[PathInput] = None,
    prepared_news: Sequence[JsonObject] = (),
) -> ExperimentRuntimeHandle:
    """Start scheduling over a trusted prepared source; creates no root or model service."""
    from sim_platform.agents.configuration import validate_run_configuration
    from sim_platform.experiments.scheduling.host import ExperimentRuntimeHandle

    config = validate_run_configuration(configuration)
    policy = dict(
        start_ns=int(config['start_ns']),
        end_ns=int(config['end_ns']),
        symbol=config['symbol'],
        event_buckets=config['limits']['event_buckets'],
        timeout_seconds=config['timeout_seconds'],
        retry=config['retry'],
        schedule=config['definition'].get('schedule')
        or dict(mode='continuous', busy_event_policy='preserve_interval', events=[]),
        paced=config['speed'] == 'turn',
    )
    if config.get('budget') is not None:
        policy['budget'] = config['budget']
    from .runtime_policy import PreparedWorkPolicy

    if prepared_artifact is None:
        raise ContractError('Runtime requires a trusted prepared source')
    policy['prepared_artifact'] = str(prepared_artifact)
    policy['prepared_news'] = list(prepared_news)
    return ExperimentRuntimeHandle(
        PreparedWorkPolicy,
        policy,
        clock=clock,
        capacity=config['limits']['pending_requests'],
        on_notice=on_notice,
        on_failure=on_failure,
    )
